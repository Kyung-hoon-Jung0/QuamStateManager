"""W3-7: Chip Status Trends' x-axis title overlapped the legend. The legend
was placed at y = -0.28 of the plot; a two-line date axis plus its title
reach further than that. It is anchored to the figure's bottom now (checked
in headless Chrome against the bundled Plotly)."""
import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "chip-status.js"


def test_the_trends_legend_sits_at_the_figure_bottom():
    src = JS.read_text(encoding="utf-8")
    i = src.index("title: axisType === 'date'")
    block = src[i - 2500:i]
    m = re.search(r"legend:\s*\{([^}]*)\}", block)
    assert m, "no legend in the Trends layout"
    leg = m.group(1)
    assert "yref: 'container'" in leg and "yanchor: 'bottom'" in leg and "y: 0" in leg, leg
