"""Conservative source dataflow check for defaults used by marker calls."""
import ast
import inspect
import textwrap


def marker_defaults(fn, report):
    fields = set()
    default_args = set()
    for name, decision in report.items():
        if decision.get("status") == "defaulted":
            default_args.add(name)
        for record in decision.get("records", []):
            fields.update(str(key).split(".")[-1] for key in record.get("defaulted", {}))
    if not fields and not default_args:
        return []
    seen = set()

    def analyze(function, inherited=()):
        if function in seen or len(seen) >= 64:
            return set(), set()
        seen.add(function)
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        except (OSError, TypeError, SyntaxError):
            # Source unavailable: the dependency cannot be excluded safely.
            return fields | default_args, fields | default_args
        names = {name: {name} for name in default_args | set(inherited)}
        helpers = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                helper = function.__globals__.get(node.func.id)
                if inspect.isfunction(helper) and (helper.__module__ == function.__module__ or
                                                  helper.__module__.startswith("calibration_utils.")):
                    helpers[node.func.id] = analyze(helper)

        def dependencies(node):
            found = set()
            if node is None:
                return found
            for part in ast.walk(node):
                if isinstance(part, ast.Name):
                    found.update(names.get(part.id, ()))
                elif isinstance(part, ast.Attribute) and part.attr in fields:
                    found.add(part.attr)
                elif isinstance(part, ast.Subscript) and isinstance(part.slice, ast.Constant):
                    if part.slice.value in fields:
                        found.add(part.slice.value)
                elif isinstance(part, ast.Call):
                    if isinstance(part.func, ast.Name):
                        found.update(helpers.get(part.func.id, (set(), set()))[1])
                    for arg in part.args:
                        if isinstance(arg, ast.Constant) and arg.value in fields:
                            found.add(arg.value)
            return found

        for _ in range(8):
            before = repr(names)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    deps = dependencies(node.value)
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name):
                                names.setdefault(name.id, set()).update(deps)
            if repr(names) == before:
                break

        markers = set()
        returns = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                returns.update(dependencies(node.value))
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in helpers:
                markers.update(helpers[node.func.id][0])
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                is_marker = method in {"axvline", "axhline", "scatter", "annotate"}
                is_marker |= method == "plot" and any(k.arg == "marker" for k in node.keywords)
                is_marker |= method == "plot" and any(isinstance(a, ast.Constant) and
                                                      isinstance(a.value, str) and "*" in a.value for a in node.args)
                if is_marker:
                    markers.update(dependencies(node))
        # A default controlling whether/how a marker is drawn also affects it.
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp)):
                has_marker = any(isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) and
                                 part.func.attr in {"axvline", "axhline", "scatter", "annotate"}
                                 for part in ast.walk(node))
                if has_marker:
                    markers.update(dependencies(node.test))
        return markers, returns

    return sorted(analyze(fn)[0])
