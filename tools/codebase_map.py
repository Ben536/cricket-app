#!/usr/bin/env python3
"""
Codebase map: a mechanical inventory of the repo that never goes stale.

Every full review of this project has started by rediscovering the same
facts by hand: which message types the phone actually sends, which the server
actually handles, which contract types nobody implements, which engine
parameters are wired into both engines, and which modules have no test at
all. This tool derives all of that from the source, so the next reviewer (or
the next model) starts from ground truth instead of from the previous
reviewer's notes.

    python3 tools/codebase_map.py            # human-readable report
    python3 tools/codebase_map.py --write    # also refresh vault/architecture/Codebase Inventory.md
    python3 tools/codebase_map.py --check    # exit 1 on drift (used by CI and scripts/check_all.sh)

"Drift" means one of the cross-references below disagrees with itself:
  - the frontend sends a message type the server has no handler for
  - the router validates a type nobody handles (or handles a type it rejects)
  - an engine parameter exists in engine_params.json but only one engine reads it
  - an engine reads a parameter the params file does not define
  - an error code appears in code but not in contracts/error_codes.md
  - a systemd unit ships in scripts/systemd/ but a deploy/install script does not enable it

Everything else (dormant handlers, untested modules, dead CSS) is reported,
not failed: those are review findings, not build breaks.

Python facts are read from the AST (comments and dead strings cannot fool
them); TypeScript facts are read after stripping comments.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories that make up the product, grouped the way the architecture docs
# describe them. Anything not listed is "other" (docs, config, generated).
AREAS = {
    "frontend": ["src", "index.html", "public"],
    "engine (python)": ["engine"],
    "server": ["server"],
    "radar": ["radar"],
    "db": ["db"],
    "ops": ["scripts"],
    "contracts": ["contracts"],
    "tools": ["tools"],
    "tests (python)": ["tests"],
    "docs": ["vault", "designs", "CLAUDE.md", "README.md",
             "CRICKETRADAR_PLAN.md", "DEVICE_ARCHITECTURE.md"],
}
SOURCE_EXT = {".py", ".ts", ".tsx", ".sql", ".sh", ".css", ".html", ".json", ".md", ".service", ".cfg"}
SKIP_DIRS = {"node_modules", ".git", "dist", ".venv", "__pycache__", ".pytest_cache", "recordings"}
SKIP_FILES = {"package-lock.json", "shots.json", "results_py.json", "results_ts.json"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def iter_files(*suffixes: str):
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SKIP_FILES:
            continue
        if suffixes and p.suffix not in suffixes:
            continue
        yield p


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return ""


def count_loc(p: Path) -> int:
    return sum(1 for line in read(p).splitlines() if line.strip())


def py_tree(p: Path):
    try:
        return ast.parse(read(p))
    except SyntaxError:
        return None


_TS_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
# A `//` that starts a line or follows whitespace. (Not one inside "ws://".)
_TS_LINE_COMMENT = re.compile(r"(^|\s)//[^\n]*")


def strip_ts_comments(text: str) -> str:
    text = _TS_BLOCK_COMMENT.sub("", text)
    return _TS_LINE_COMMENT.sub(r"\1", text)


def sh_code_lines(text: str) -> list[str]:
    """Non-comment lines of a shell script."""
    return [line for line in text.splitlines() if not line.strip().startswith("#")]


# ---------------------------------------------------------------------------
# 1. size by area
# ---------------------------------------------------------------------------

def loc_by_area() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in iter_files(*SOURCE_EXT):
        r = rel(p)
        area = "other"
        for name, roots in AREAS.items():
            if any(r == root or r.startswith(root + "/") for root in roots):
                area = name
                break
        out[area][p.suffix] += count_loc(p)
    return out


# ---------------------------------------------------------------------------
# 2. python import graph (intra-repo only)
# ---------------------------------------------------------------------------

PY_PACKAGES = {"engine", "server", "radar", "db", "contracts", "tools", "scripts", "tests"}


def python_modules() -> dict[str, Path]:
    mods = {}
    for p in iter_files(".py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[0] not in PY_PACKAGES:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mods[".".join(parts)] = p
    return mods


def python_import_graph(mods: dict[str, Path]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for mod, path in mods.items():
        tree = py_tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
                # `from X import Y` where X.Y is itself a module
                names += [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                top = n.split(".")[0]
                if top in PY_PACKAGES:
                    target = n
                elif f"scripts.{n}" in mods:
                    # Scripts/tests import a sibling by bare name after sys.path hacks
                    target = f"scripts.{n}"
                else:
                    continue
                # resolve to the longest known module prefix
                while target and target not in mods:
                    target = target.rpartition(".")[0]
                if target and target != mod:
                    graph[mod].add(target)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str):
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, ()):
            if state.get(m) == 1:
                cycles.append(stack[stack.index(m):] + [m])
            elif state.get(m) is None:
                visit(m)
        stack.pop()
        state[n] = 2

    for n in graph:
        if state.get(n) is None:
            visit(n)
    return cycles


# ---------------------------------------------------------------------------
# 3. typescript import graph
# ---------------------------------------------------------------------------

TS_IMPORT_RE = re.compile(r"""^\s*import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""", re.M)


def ts_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    files = [p for p in iter_files(".ts", ".tsx") if rel(p).startswith(("src/", "tools/parity/"))]
    for p in files:
        deps = set()
        for target in TS_IMPORT_RE.findall(strip_ts_comments(read(p))):
            if not target.startswith("."):
                continue
            resolved = (p.parent / target).resolve()
            candidates = [resolved] + [resolved.with_suffix(s) for s in (".ts", ".tsx", ".json")]
            candidates += [resolved / "index.ts", resolved / "index.tsx"]
            for c in candidates:
                if c.is_file():
                    deps.add(rel(c))
                    break
            else:
                deps.add(target + "  (UNRESOLVED)")
        graph[rel(p)] = deps
    return graph


# ---------------------------------------------------------------------------
# 4. websocket message cross-reference
# ---------------------------------------------------------------------------

def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def registered_handler_types(path: Path) -> set[str]:
    """register_handler("<type>", ...) calls, from the AST."""
    out = set()
    tree = py_tree(path)
    if tree is None:
        return out
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register_handler" and node.args):
            t = _const_str(node.args[0])
            if t:
                out.add(t)
    return out


def assigned_string_set(path: Path, name: str) -> set[str]:
    """The string constants in `NAME = { ... }` / `[ ... ]`."""
    tree = py_tree(path)
    if tree is None:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
                return {s for s in (_const_str(e) for e in node.value.elts) if s}
    return set()


def emitted_message_types(paths) -> set[str]:
    """Dict literals with a "type": "<x>" entry, from the AST."""
    out = set()
    for p in paths:
        tree = py_tree(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if k is not None and _const_str(k) == "type":
                        t = _const_str(v)
                        if t:
                            out.add(t)
    return out


def message_xref() -> dict[str, set[str]]:
    src = ROOT / "src"
    server = ROOT / "server"
    xref: dict[str, set[str]] = defaultdict(set)

    xref["server_registered"] = registered_handler_types(server / "handlers.py")
    xref["router_valid"] = assigned_string_set(server / "message_router.py", "VALID_CLIENT_TYPES")
    xref["server_emits"] = emitted_message_types(server.glob("*.py"))

    # frontend: sent types (sendMessage('x') and raw type: 'x') and consumed types
    sent, consumed = set(), set()
    for p in src.rglob("*.ts*"):
        if "__tests__" in p.parts:
            continue
        text = strip_ts_comments(read(p))
        sent |= set(re.findall(r"sendMessage\(\s*['\"]([a-z_]+)['\"]", text))
        sent |= set(re.findall(r"type:\s*['\"]([a-z_]+)['\"]\s*,\s*\n?\s*message_id", text))
        consumed |= set(re.findall(r"type\s*===\s*['\"]([a-z_]+)['\"]", text))
    xref["frontend_sends"] = sent
    xref["frontend_consumes"] = consumed

    # contracts
    ts_contract = strip_ts_comments(read(ROOT / "contracts" / "api_types.ts"))
    xref["contract_ts_types"] = set(re.findall(r'^\s*type:\s*"([a-z_]+)"', ts_contract, re.M))
    try:
        proto = json.loads(read(ROOT / "contracts" / "websocket_protocol.json"))
        xref["contract_json_client"] = set(_message_keys(proto.get("clientToServer", {})))
        xref["contract_json_server"] = set(_message_keys(proto.get("serverToClient", {})))
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    return xref


def _message_keys(section) -> list[str]:
    """websocket_protocol.json nests messages differently per section; collect
    every `type` const/enum it declares rather than assuming a layout."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            t = o.get("type")
            if isinstance(t, dict):
                if "const" in t:
                    found.append(t["const"])
                for e in t.get("enum", []):
                    found.append(e)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(section)
    return found


# ---------------------------------------------------------------------------
# 5. engine parameter cross-reference
# ---------------------------------------------------------------------------

def py_params_used(path: Path, name: str = "_PARAMS") -> set[str]:
    """_PARAMS["x"], _PARAMS['x'] and _PARAMS.get("x") from the AST."""
    out = set()
    tree = py_tree(path)
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == name:
            k = _const_str(node.slice)
            if k:
                out.add(k)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == name
              and node.func.attr == "get" and node.args):
            k = _const_str(node.args[0])
            if k:
                out.add(k)
    return out


def ts_params_used(text: str, name: str = "PARAMS") -> set[str]:
    """PARAMS.x and PARAMS['x'] / PARAMS["x"], comments stripped."""
    text = strip_ts_comments(text)
    used = set(re.findall(rf"\b{name}\.([a-z_][a-z0-9_]*)", text))
    used |= set(re.findall(rf"\b{name}\[['\"]([a-z_][a-z0-9_]*)['\"]\]", text))
    return used


def engine_params_xref() -> dict[str, set[str]]:
    params = json.loads(read(ROOT / "engine" / "engine_params.json"))
    keys = {k for k in params if not k.startswith("_")}
    ts_used = ts_params_used(read(ROOT / "src" / "gameEngine.ts")) | ts_params_used(read(ROOT / "src" / "fieldZones.ts"))
    py_used = py_params_used(ROOT / "engine" / "game_engine.py")
    return {"defined": keys, "ts_used": ts_used, "py_used": py_used}


# ---------------------------------------------------------------------------
# 6. tests -> modules
# ---------------------------------------------------------------------------

def test_coverage_map(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    """Only DIRECT imports count as 'has a test' - transitive coverage hides gaps."""
    covered: dict[str, set[str]] = defaultdict(set)
    for mod, deps in graph.items():
        if not mod.startswith("tests."):
            continue
        for d in deps:
            if not d.startswith("tests."):
                covered[d].add(mod)
    return covered


def vitest_files() -> list[str]:
    return sorted(rel(p) for p in iter_files(".ts", ".tsx") if ".test." in p.name)


# ---------------------------------------------------------------------------
# 7. error codes
# ---------------------------------------------------------------------------

def error_code_xref() -> dict[str, set[str]]:
    in_code = set()
    for p in list((ROOT / "server").glob("*.py")) + list((ROOT / "db").glob("*.py")):
        tree = py_tree(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            s = _const_str(node)
            if s and re.fullmatch(r"E\d{4}", s):
                in_code.add(s)
    documented = set(re.findall(r"\b(E\d{4})\b", read(ROOT / "contracts" / "error_codes.md")))
    return {"in_code": in_code, "documented": documented}


# ---------------------------------------------------------------------------
# 8. systemd units
# ---------------------------------------------------------------------------

def systemd_xref() -> dict[str, set[str]]:
    units = {p.name for p in (ROOT / "scripts" / "systemd").glob("*.service")}
    deploy_code = "\n".join(sh_code_lines(read(ROOT / "scripts" / "deploy_to_pi.sh")))
    install_code = "\n".join(sh_code_lines(read(ROOT / "scripts" / "install_services.sh")))
    # The deploy enables units BY NAME; a `cp *.service` glob installs the
    # file but does not enable it, so a name is required.
    enabled_by_deploy = {u for u in units if u.removesuffix(".service") in deploy_code}
    # The on-device installer loops over every shipped unit
    install_all = '"$UNIT_DIR"/*.service' in install_code
    enabled_by_install = units if install_all else {u for u in units if u.removesuffix(".service") in install_code}
    return {
        "units": units,
        "enabled_by_deploy": enabled_by_deploy,
        "enabled_by_install": enabled_by_install,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def build_report() -> tuple[str, list[str]]:
    lines: list[str] = []
    drift: list[str] = []

    def h(title: str):
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")

    lines.append("# Codebase Inventory (generated)")
    lines.append("")
    lines.append("Generated by `python3 tools/codebase_map.py --write`. Do not edit by hand;")
    lines.append("re-run after structural changes. `--check` fails CI on the drift rules listed")
    lines.append("in the tool's docstring. Narrative lives in [[Codebase Map]].")

    # --- size
    h("Size by area (non-blank lines)")
    areas = loc_by_area()
    lines.append("| area | total | breakdown |")
    lines.append("|---|---:|---|")
    for area in list(AREAS) + ["other"]:
        if area not in areas:
            continue
        counts = areas[area]
        total = sum(counts.values())
        breakdown = ", ".join(f"{ext} {n}" for ext, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append(f"| {area} | {total} | {breakdown} |")

    # --- python graph
    mods = python_modules()
    graph = python_import_graph(mods)
    h("Python import graph (intra-repo)")
    for mod in sorted(graph):
        if mod.startswith("tests."):
            continue
        deps = sorted(d for d in graph[mod] if not d.startswith("tests."))
        lines.append(f"- `{mod}` -> {', '.join(f'`{d}`' for d in deps) if deps else '(leaf)'}")
    cycles = find_cycles({m: d for m, d in graph.items() if not m.startswith("tests.")})
    if cycles:
        lines.append("")
        lines.append("Import cycles (runtime imports inside functions may hide these from Python but not from a reader):")
        for c in cycles:
            lines.append(f"- {' -> '.join(c)}")

    # --- ts graph
    h("TypeScript import graph")
    for f, deps in sorted(ts_import_graph().items()):
        lines.append(f"- `{f}` -> {', '.join(f'`{d}`' for d in sorted(deps)) if deps else '(leaf)'}")
        for d in deps:
            if "UNRESOLVED" in d:
                drift.append(f"TS import unresolved: {f} -> {d}")

    # --- messages
    h("WebSocket message cross-reference")
    x = message_xref()
    live = x["frontend_sends"] & x["server_registered"]
    dormant = x["server_registered"] - x["frontend_sends"]
    unhandled = x["frontend_sends"] - x["server_registered"]
    lines.append(f"- Client->server types the phone SENDS and the server HANDLES (the live path): {sorted(live)}")
    lines.append(f"- Server handlers the phone never exercises (dormant): {sorted(dormant)}")
    if unhandled:
        lines.append(f"- **Sent by the phone but NO server handler**: {sorted(unhandled)}")
        drift += [f"frontend sends '{t}' but server registers no handler" for t in sorted(unhandled)]
    not_validated = x["server_registered"] - x["router_valid"]
    not_handled = x["router_valid"] - x["server_registered"]
    if not_validated:
        drift += [f"handler registered for '{t}' but router VALID_CLIENT_TYPES rejects it" for t in sorted(not_validated)]
    if not_handled:
        drift += [f"router accepts '{t}' but no handler is registered" for t in sorted(not_handled)]
    lines.append(f"- Server->client types emitted: {sorted(x['server_emits'])}")
    lines.append(f"- Server->client types the phone checks for: {sorted(x['frontend_consumes'])}")
    never_consumed = x["server_emits"] - x["frontend_consumes"]
    lines.append(f"- Emitted but never branched on by the phone (informational pushes or dormant): {sorted(never_consumed)}")
    if x.get("contract_ts_types"):
        undeclared_client = live - x["contract_ts_types"]
        lines.append(f"- Live client types missing from contracts/api_types.ts: {sorted(undeclared_client)}")
        undeclared_server = x["server_emits"] - x["contract_ts_types"]
        lines.append(f"- Emitted server types missing from contracts/api_types.ts: {sorted(undeclared_server)}")
    if x.get("contract_json_client"):
        lines.append(f"- websocket_protocol.json client types: {sorted(x['contract_json_client'])}")
        lines.append(f"- websocket_protocol.json server types: {sorted(x['contract_json_server'])}")
        lines.append(f"- In protocol.json but unimplemented server-side: {sorted(x['contract_json_client'] - x['server_registered'])}")
        lines.append(f"- Implemented server-side but absent from protocol.json: {sorted(x['server_registered'] - x['contract_json_client'])}")

    # --- engine params
    h("Engine parameters (engine/engine_params.json)")
    ep = engine_params_xref()
    both = ep["defined"] & ep["ts_used"] & ep["py_used"]
    lines.append(f"- Defined: {len(ep['defined'])}; read by BOTH engines: {len(both)}")
    only_ts = (ep["defined"] & ep["ts_used"]) - ep["py_used"]
    only_py = (ep["defined"] & ep["py_used"]) - ep["ts_used"]
    unused = ep["defined"] - ep["ts_used"] - ep["py_used"]
    missing = (ep["ts_used"] | ep["py_used"]) - ep["defined"]
    for label, s in (("Read by TS only", only_ts), ("Read by Python only", only_py),
                     ("Defined but unused", unused), ("Used but undefined", missing)):
        lines.append(f"- {label}: {sorted(s) if s else 'none'}")
    for k in sorted(only_ts | only_py):
        drift.append(f"engine param '{k}' is read by only one engine (fork risk)")
    for k in sorted(missing):
        drift.append(f"engine param '{k}' is read but not defined in engine_params.json")

    # --- tests
    h("Test coverage map (which test file imports which module)")
    covered = test_coverage_map(graph)
    product_mods = sorted(m for m in mods if not m.startswith(("tests.", "tools.")) and mods[m].name != "__init__.py")
    for m in product_mods:
        tests = sorted(covered.get(m, ()))
        lines.append(f"- `{m}`: {', '.join(f'`{t}`' for t in tests) if tests else '**no direct test**'}")
    lines.append("")
    lines.append(f"- vitest files: {vitest_files()}")

    # --- error codes
    h("Error codes")
    ec = error_code_xref()
    undocumented = ec["in_code"] - ec["documented"]
    lines.append(f"- Used in code: {len(ec['in_code'])}; documented: {len(ec['documented'])}")
    lines.append(f"- Used but undocumented: {sorted(undocumented) if undocumented else 'none'}")
    lines.append(f"- Documented but unused: {sorted(ec['documented'] - ec['in_code'])}")
    for c in sorted(undocumented):
        drift.append(f"error code {c} used in code but not documented in contracts/error_codes.md")

    # --- systemd
    h("systemd units")
    su = systemd_xref()
    lines.append(f"- Units shipped: {sorted(su['units'])}")
    lines.append(f"- Enabled by deploy_to_pi.sh: {sorted(su['enabled_by_deploy'])}")
    lines.append(f"- Enabled by install_services.sh: {sorted(su['enabled_by_install'])}")
    for u in sorted(su["units"] - su["enabled_by_deploy"]):
        drift.append(f"systemd unit {u} is shipped but deploy_to_pi.sh does not enable it")
    for u in sorted(su["units"] - su["enabled_by_install"]):
        drift.append(f"systemd unit {u} is shipped but install_services.sh does not enable it")

    return "\n".join(lines) + "\n", drift


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--write", action="store_true", help="write vault/architecture/Codebase Inventory.md")
    ap.add_argument("--check", action="store_true", help="exit 1 if any drift rule fires")
    ap.add_argument("--quiet", action="store_true", help="suppress the report (with --check)")
    args = ap.parse_args()

    report, drift = build_report()
    if not args.quiet:
        print(report)
    if args.write:
        out = ROOT / "vault" / "architecture" / "Codebase Inventory.md"
        out.write_text(report, encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}", file=sys.stderr)
    if drift:
        print("DRIFT:", file=sys.stderr)
        for d in drift:
            print(f"  - {d}", file=sys.stderr)
        if args.check:
            return 1
    elif args.check:
        print("codebase_map: no drift", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
