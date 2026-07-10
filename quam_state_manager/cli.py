"""Command-line interface for QUAM State Manager.

Built with typer (auto-help, type hints) + rich (pretty tables/panels).

Usage::

    quam-manager show qA1
    quam-manager show qA1 --section readout
    quam-manager show qA1-A2
    quam-manager table f_01 T2ramsey gate_fidelity_avg
    quam-manager wiring
    quam-manager search "7639"
    quam-manager set qubits.qA1.f_01 6.3e9
    quam-manager save
    quam-manager diff ./state_old/ ./state_new/
    quam-manager export summary.csv
    quam-manager scan ./data/project_name/
    quam-manager trend f_01 --folder ./data/ --qubits qA1,qA2
"""

from __future__ import annotations

import json as _json
import math
import re
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from quam_state_manager import __version__
from quam_state_manager.core import units
from quam_state_manager.core.differ import Differ
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.core.saver import Saver
from quam_state_manager.core.scanner import Workspace
from quam_state_manager.core.search_index import SearchIndex


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"quam-manager {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="quam-manager",
    help="QUAM State Manager -- search, inspect, edit, and compare quantum machine configurations.",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _main_callback(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Print the installed quam-manager version and exit.",
    ),
) -> None:
    """Top-level callback so --version works without a subcommand."""
    return


def _json_dump(payload: Any) -> None:
    """Print *payload* as compact JSON to stdout (suitable for scripting)."""

    def _default(o):
        if isinstance(o, Path):
            return str(o)
        return repr(o)

    typer.echo(_json.dumps(payload, default=_default, ensure_ascii=False))

_SECTION_KEYS: dict[str, list[str]] = {
    "frequency": ["f_01", "f_12", "anharmonicity", "chi", "xy_RF_frequency", "readout_frequency", "readout_RF_frequency"],
    "coherence": ["T1", "T2ramsey", "T2echo"],
    "xy": ["x180_amplitude", "x180_length", "x180_alpha", "x90_amplitude", "saturation_amplitude", "xy_RF_frequency", "xy_intermediate_frequency"],
    "readout": ["readout_frequency", "readout_amplitude", "readout_length", "readout_threshold", "readout_iw_angle", "readout_RF_frequency", "confusion_matrix", "time_of_flight"],
    "flux": ["z_joint_offset", "z_independent_offset", "z_flux_point", "freq_vs_flux_01_quad_term", "phi0_current", "phi0_voltage"],
    "fidelity": ["gate_fidelity_avg", "gate_fidelity_x180", "gate_fidelity_x90"],
}


def _load_store(folder: Path) -> QuamStore:
    try:
        return QuamStore(folder)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _format_cell(value, field: str | None = None) -> str:
    if value is None:
        return "[dim]N/A[/dim]"
    # Humanize known physical fields (T1->µs, f_01->GHz, …) for the Rich tables.
    # --json paths bypass this and stay raw SI for scripting.
    if field is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        fq = units.format_quantity(value, field)
        if fq is not None:
            return f"{fq[0]} {fq[1]}"
    if isinstance(value, float):
        if abs(value) >= 1e6 or (0 < abs(value) < 1e-3):
            return f"{value:.6e}"
        return f"{value:.6f}"
    if isinstance(value, list):
        if len(value) <= 4 and all(not isinstance(v, (list, dict)) for v in value):
            return str(value)
        return f"[dim]list[{len(value)}][/dim]"
    if isinstance(value, dict):
        return f"[dim]dict[{len(value)}][/dim]"
    return str(value)


# ------------------------------------------------------------------
# serve — run the web UI in a browser
# ------------------------------------------------------------------


@app.command()
def serve(
    port: int = typer.Option(5050, "--port", "-p", help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    debug: bool = typer.Option(False, "--debug", help="Flask debug mode (auto-reload)"),
) -> None:
    """Run the web UI in your browser at http://HOST:PORT.

    The desktop app (its own window) is ``python -m quam_state_manager``; this is
    the browser alternative — handy when the pywebview/WebView2 window won't open.
    """
    from quam_state_manager.web.app import create_app

    typer.echo(f"QUAM State Manager — open  http://{host}:{port}   (Ctrl+C to quit)")
    create_app().run(host=host, port=port, debug=debug)


# ------------------------------------------------------------------
# browser — `qsm browser`: run the web UI and pop the browser open
# ------------------------------------------------------------------


@app.command()
def browser(
    port: int = typer.Option(5050, "--port", "-p", help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    debug: bool = typer.Option(False, "--debug", help="Flask debug mode (auto-reload)"),
    no_open: bool = typer.Option(
        False, "--no-open", help="Don't auto-open the browser; just print the URL"
    ),
) -> None:
    """Launch the web UI and open it in your default browser (``qsm browser``).

    Same server as ``serve``, but it also pops your browser open at the URL —
    the simplest way to run the app in a browser.
    """
    import threading
    import webbrowser

    from quam_state_manager.web.app import create_app

    url = f"http://{host}:{port}"
    typer.echo(f"QUAM State Manager — opening  {url}   (Ctrl+C to quit)")

    if not no_open:
        # Open after a short delay so the server is accepting connections.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    create_app().run(host=host, port=port, debug=debug)


# ------------------------------------------------------------------
# show
# ------------------------------------------------------------------


@app.command()
def show(
    name: str = typer.Argument(help="Qubit ID (e.g. qA1) or pair ID (e.g. qA1-A2)"),
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    section: str | None = typer.Option(None, "--section", "-s", help="Filter section: frequency, coherence, xy, readout, flux, fidelity"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a Rich table (scripting)."),
):
    """Show all properties of a qubit or qubit pair."""
    store = _load_store(folder)
    engine = QueryEngine(store)

    is_pair = "-" in name and name not in store.qubit_names

    try:
        if is_pair:
            data = engine.get_pair(name)
        else:
            data = engine.get_qubit(name)
    except KeyError as e:
        if as_json:
            _json_dump({"error": str(e), "name": name})
        else:
            console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if section and not is_pair:
        allowed_keys = _SECTION_KEYS.get(section)
        if allowed_keys is None:
            if as_json:
                _json_dump({"error": f"unknown section: {section}", "available": list(_SECTION_KEYS.keys())})
            else:
                console.print(f"[red]Unknown section:[/red] {section}. Available: {', '.join(_SECTION_KEYS.keys())}")
            raise typer.Exit(1)
        data = {k: v for k, v in data.items() if k in allowed_keys or k == "id"}

    if as_json:
        _json_dump({"type": "pair" if is_pair else "qubit", "name": name, "data": data})
        return

    table = Table(title=f"{'Pair' if is_pair else 'Qubit'}: {name}", show_lines=True)
    table.add_column("Property", style="cyan", min_width=20)
    table.add_column("Value", min_width=30)

    for key, value in data.items():
        table.add_row(key, _format_cell(value, key))

    console.print(table)


# ------------------------------------------------------------------
# table
# ------------------------------------------------------------------


@app.command()
def table(
    properties: list[str] = typer.Argument(help="Property names (e.g. f_01 T2ramsey gate_fidelity_avg)"),
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a Rich table (scripting)."),
):
    """Show a comparison table of selected properties across all qubits."""
    store = _load_store(folder)
    engine = QueryEngine(store)
    rows = engine.summary_table(properties)

    if as_json:
        _json_dump({"properties": properties, "rows": rows})
        return

    t = Table(title="Qubit Comparison", show_lines=True)
    t.add_column("id", style="cyan bold")
    for prop in properties:
        t.add_column(prop, justify="right")

    for row in rows:
        t.add_row(row["id"], *[_format_cell(row.get(p), p) for p in properties])

    console.print(t)


# ------------------------------------------------------------------
# wiring
# ------------------------------------------------------------------


@app.command()
def wiring(
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
):
    """Show the full port wiring map for all qubits."""
    store = _load_store(folder)
    engine = QueryEngine(store)
    rows = engine.get_wiring_map()

    t = Table(title="Wiring Map", show_lines=True)
    if not rows:
        console.print("[yellow]No wiring data found.[/yellow]")
        return

    cols = list(rows[0].keys())
    for col in cols:
        t.add_column(col, style="cyan" if col == "qubit" else "")

    for row in rows:
        t.add_row(*[_format_cell(row.get(c)) for c in cols])

    console.print(t)


# ------------------------------------------------------------------
# search
# ------------------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(help="Search query (e.g. '7639', 'qA1 readout', 'T2')"),
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    limit: int = typer.Option(30, "--limit", "-n", help="Max results"),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter: qubit, pair, wiring, other"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a Rich table (scripting)."),
):
    """Search all values and keys in the QUAM state."""
    store = _load_store(folder)
    index = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    results = index.search(query, limit=limit, category=category)

    if as_json:
        _json_dump({
            "query": query,
            "count": len(results),
            "results": [
                {"dot_path": r.dot_path, "value": r.raw_value, "score": r.score, "category": r.category}
                for r in results
            ],
        })
        return

    if not results:
        console.print(f"[yellow]No results for:[/yellow] {query!r}")
        return

    t = Table(title=f"Search: {query!r} ({len(results)} results)", show_lines=True)
    t.add_column("Path", style="cyan", max_width=60)
    t.add_column("Value", max_width=40)
    t.add_column("Score", justify="right", style="dim")

    for r in results:
        t.add_row(r.dot_path, _format_cell(r.raw_value), f"{r.score:.1f}")

    console.print(t)


# ------------------------------------------------------------------
# set
# ------------------------------------------------------------------


@app.command(name="set")
def set_value(
    dot_path: str = typer.Argument(help="Dot-separated path (e.g. qubits.qA1.f_01)"),
    value: str = typer.Argument(help="New value"),
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    save_now: bool = typer.Option(False, "--save", help="Save immediately after setting"),
):
    """Set a single value by dot-path."""
    store = _load_store(folder)
    mod = Modifier(store)

    parsed = _parse_value(value)

    try:
        entry = mod.set_value(dot_path, parsed)
    except (KeyError, TypeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[cyan]{entry.dot_path}[/cyan]\n"
        f"  old: {_format_cell(entry.old_value)}\n"
        f"  new: [green]{_format_cell(entry.new_value)}[/green]\n"
        f"  file: {entry.source_file}.json",
        title="Value Set",
    ))

    if save_now:
        saver = Saver(store)
        saver.save()
        console.print("[green]Saved.[/green]")


# ------------------------------------------------------------------
# save
# ------------------------------------------------------------------


@app.command()
def save(
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save to a different folder"),
):
    """Save the current state to disk (with automatic backup)."""
    store = _load_store(folder)

    if not store.change_log:
        console.print("[yellow]No unsaved changes.[/yellow]")
        return

    saver = Saver(store)
    target = saver.save(output)
    console.print(f"[green]Saved to {target}[/green] ({len(store.change_log)} changes written)")


# ------------------------------------------------------------------
# diff
# ------------------------------------------------------------------


@app.command()
def diff(
    path_a: Path = typer.Argument(help="First quam_state folder"),
    path_b: Path = typer.Argument(help="Second quam_state folder"),
    tolerance: float = typer.Option(1e-12, "--tolerance", "-t", help="Float comparison tolerance"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to display"),
):
    """Compare two quam_state folders and show differences."""
    differ = Differ()

    try:
        entries = differ.diff(path_a, path_b, float_tolerance=tolerance)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    summary = Differ.summary(entries)
    console.print(Panel(
        f"Added: [green]{summary['added']}[/green]  "
        f"Removed: [red]{summary['removed']}[/red]  "
        f"Modified: [yellow]{summary['modified']}[/yellow]  "
        f"Total: {summary['total']}",
        title="Diff Summary",
    ))

    if not entries:
        console.print("[green]No differences found.[/green]")
        return

    t = Table(show_lines=True)
    t.add_column("Type", width=8)
    t.add_column("Path", style="cyan", max_width=55)
    t.add_column("Old", max_width=25)
    t.add_column("New", max_width=25)

    type_style = {"added": "green", "removed": "red", "modified": "yellow"}

    for entry in entries[:limit]:
        style = type_style.get(entry.change_type, "")
        t.add_row(
            f"[{style}]{entry.change_type}[/{style}]",
            entry.dot_path,
            _format_cell(entry.old_value),
            _format_cell(entry.new_value),
        )

    console.print(t)

    if len(entries) > limit:
        console.print(f"[dim]... and {len(entries) - limit} more entries (use --limit to show more)[/dim]")


# ------------------------------------------------------------------
# export
# ------------------------------------------------------------------


@app.command()
def export(
    output: Path = typer.Argument(help="Output file path (.csv or .md)"),
    folder: Path = typer.Option(".", "--folder", "-f", help="Path to quam_state folder"),
    properties: list[str] | None = typer.Option(None, "--props", "-p", help="Properties to export (defaults to standard set)"),
    raw: bool = typer.Option(False, "--raw", help="Emit raw SI values with bare headers (no unit conversion/labels) for legacy pipelines."),
):
    """Export qubit summary as CSV or Markdown.

    By default, dimensioned columns are unit-labeled and converted to display
    units (``f_01_GHz``, ``T1_us``). Use ``--raw`` for the legacy raw-SI format.
    """
    store = _load_store(folder)
    saver = Saver(store)

    suffix = output.suffix.lower()
    if suffix == ".csv":
        saver.export_csv(output, properties=properties, with_units=not raw)
    elif suffix in (".md", ".markdown"):
        saver.export_markdown(output, properties=properties, with_units=not raw)
    else:
        console.print(f"[red]Unsupported format:[/red] {suffix}. Use .csv or .md")
        raise typer.Exit(1)

    console.print(f"[green]Exported to {output}[/green]")


# ------------------------------------------------------------------
# scan
# ------------------------------------------------------------------


@app.command()
def scan(
    folders: list[Path] = typer.Argument(help="Root folders to scan for quam_state directories"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to display"),
    date: str | None = typer.Option(None, "--date", "-d", help="Filter by date (e.g. 2026-02-19)"),
    name: str | None = typer.Option(None, "--name", help="Filter by experiment name substring"),
):
    """Scan folder trees for quam_state directories and list experiments."""
    ws = Workspace()
    total = 0

    for folder in folders:
        if not folder.exists():
            console.print(f"[yellow]Warning: {folder} does not exist, skipping.[/yellow]")
            continue
        entries = ws.add_root(folder)
        total += len(entries)

    flat = ws.get_flat_list(date_filter=date, experiment_filter=name)

    if not flat:
        console.print("[yellow]No quam_state folders found.[/yellow]")
        return

    console.print(f"[bold]Found {len(flat)} experiment(s)[/bold] (scanned {total} total)")

    t = Table(show_lines=True)
    t.add_column("#", style="dim", width=4)
    t.add_column("Date", style="cyan", width=12)
    t.add_column("Name", min_width=30)
    t.add_column("Time", width=10)
    t.add_column("Qubits", width=8, justify="right")
    t.add_column("Path", style="dim", max_width=50)

    for i, entry in enumerate(flat[:limit]):
        t.add_row(
            str(entry.run_id) if entry.run_id is not None else "-",
            entry.date_str or "-",
            entry.experiment_name or entry.quam_state_path.parent.name,
            entry.timestamp.split("T")[-1][:8] if entry.timestamp and "T" in entry.timestamp else "-",
            str(len(entry.qubits)) if entry.qubits else "-",
            str(entry.quam_state_path),
        )

    console.print(t)

    if len(flat) > limit:
        console.print(f"[dim]... and {len(flat) - limit} more (use --limit to show all)[/dim]")


# ------------------------------------------------------------------
# trend
# ------------------------------------------------------------------


@app.command()
def trend(
    properties: list[str] = typer.Argument(help="Properties to track (e.g. f_01 T2ramsey)"),
    folder: Path = typer.Option(..., "--folder", "-f", help="Root folder to scan"),
    qubits: str | None = typer.Option(None, "--qubits", "-q", help="Comma-separated qubit IDs (default: all)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max snapshots to compare"),
):
    """Show how properties change across experiment snapshots."""
    ws = Workspace()

    if not folder.exists():
        console.print(f"[red]Error: {folder} does not exist.[/red]")
        raise typer.Exit(1)

    ws.add_root(folder)
    flat = ws.get_flat_list()

    if not flat:
        console.print("[yellow]No quam_state folders found.[/yellow]")
        return

    selected = flat[:limit]
    stores = [ws.load_store(e.quam_state_path) for e in selected]
    labels = [
        f"#{e.run_id} {e.name or '?'}" if e.run_id else e.quam_state_path.parent.name
        for e in selected
    ]

    qubit_filter = [q.strip() for q in qubits.split(",")] if qubits else None

    differ = Differ()
    results = differ.multi_compare(stores, labels, properties, qubit_filter=qubit_filter)

    if not results:
        console.print("[yellow]No trend data found.[/yellow]")
        return

    for r in results:
        t = Table(title=f"{r['qubit']} / {r['property']}", show_lines=True)
        t.add_column("Experiment", min_width=30)
        t.add_column("Value", justify="right", min_width=20)

        for v in r["values"]:
            t.add_row(v["label"], _format_cell(v["value"]))

        console.print(t)
        console.print()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


# A digits-and-commas number with an optional decimal tail: "5,075,187,484",
# "24,000", "5050000000", and crucially MIXED/loose grouping like "7,662,072100"
# (users mix comma + plain). Commas anywhere among the integer digits are treated
# as grouping and stripped. Must start with a digit (after an optional sign) so a
# genuine string ("a,b", "MW,FEM", a "#/..." pointer) is left untouched.
_GROUPED_NUMBER = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?$")


def _parse_value(raw: str):
    """Parse a CLI / web-edit string value into the appropriate Python type.

    Symmetric with :func:`units.group_digits`: a comma-grouped number like
    ``"5,075,187,484"`` has its grouping commas stripped before numeric parsing
    (guarded so genuine comma-bearing strings are left untouched). Non-finite
    floats (``inf``/``nan``/overflow) are rejected with ``ValueError`` so a hostile
    value can never reach the JSON store as ``Infinity`` (invalid strict JSON).
    """
    s = raw.strip()
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    # Explicit JSON literal — a quoted string, an array, or an object. This lets a
    # user type a genuine string (``"02"`` → the 2-char string ``02``, never the
    # int ``2`` nor the double-quoted ``"\"02\""`` the bare-string fallback used to
    # produce) or enter a list / dict value the scalar parser can't express — a
    # ``confusion_matrix`` ``[[..],[..]]``, an ``exponential_filter`` list, etc.
    # Only attempted for these lead characters, so bare numbers / words / ``#``
    # pointers / grouped numbers keep their existing int→float→string handling; a
    # malformed literal (``[1,2``) falls through to the bare-string path.
    if s[:1] in ('"', '[', '{'):
        try:
            return _json.loads(s)
        except (ValueError, TypeError):
            pass
    candidate = s.replace(",", "") if ("," in s and _GROUPED_NUMBER.match(s)) else s
    try:
        return int(candidate)
    except ValueError:
        pass
    try:
        f = float(candidate)
    except (ValueError, OverflowError):
        return raw
    if not math.isfinite(f):
        raise ValueError(f"{raw!r} is not a finite number")
    return f


def main():
    app()


if __name__ == "__main__":
    main()
