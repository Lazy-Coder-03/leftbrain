"""`MODE_PARAMS` in each core module must match what that module's code actually reads.

The map is what `check_params` refuses against (#28 §2a), so a mode that starts reading a new
parameter without listing it would refuse a call it should answer. Rather than trusting anyone
to remember, this derives the map from the source and compares.
"""

import ast
import importlib
import pathlib

import pytest

PARAMS_NAMES = {"p", "params"}

MODULES = [
    "mathx", "datetimex", "scale", "convert", "holidays_", "numbers", "finance",
    "text", "collections_", "validate", "random_", "geo_offline", "encode", "color",
]
#: Modules whose public entry function is not named after the module.
ENTRY = {"holidays_": "holidays", "collections_": "collections", "random_": "random_tool",
         "datetimex": "datetime_tool", "mathx": "math"}


"""Derive, per mode, every parameter name the code actually reads."""

PARAMS_NAMES = {"p", "params"}

def reads_of(fn):
    """Names read from a dict-ish `p`: p.get("x"), p["x"], "x" in p.

    Also picks up `p.get(key)` where `key` is a parameter with a string default, which is
    how the shared helpers name their field (`def _items(p, key="items")`).
    """
    out = set()
    defaults = {}
    args = fn.args.args + fn.args.kwonlyargs
    vals = list(fn.args.defaults) + list(fn.args.kw_defaults)
    for a, d in zip(args[len(args) - len(vals):], vals, strict=True):
        if isinstance(d, ast.Constant) and isinstance(d.value, str):
            defaults[a.arg] = d.value
    for node in ast.walk(fn):
        # only reads *from the params dict*, which is always called p (or params)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in PARAMS_NAMES:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    out.add(node.args[0].value)
                elif node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in defaults:
                    out.add(defaults[node.args[0].id])
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if isinstance(node.value, ast.Name) and node.value.id in PARAMS_NAMES:
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    out.add(node.slice.value)
                elif isinstance(node.slice, ast.Name) and node.slice.id in defaults:
                    out.add(defaults[node.slice.id])
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            if any(isinstance(o, ast.In) for o in node.ops) and any(
                isinstance(c, ast.Name) and c.id in PARAMS_NAMES for c in node.comparators
            ):
                out.add(node.left.value)
        # _num(p, "rate"), _text(p, "a"), parse_number(p.get(...)) etc.
        # helpers that take the dict and a field name: _num(p, "rate"), _text(p, "a")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args:
            if isinstance(node.args[0], ast.Name) and node.args[0].id in PARAMS_NAMES:
                for a in node.args[1:]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        out.add(a.value)
    return out

def calls_of(fn, known):
    return {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in known}

def derive(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    direct = {name: reads_of(fn) for name, fn in fns.items()}
    calls = {name: calls_of(fn, set(fns)) for name, fn in fns.items()}
    def closure(name, seen=None, skip=frozenset()):
        seen = seen or set()
        if name in seen or name in skip:
            return set()
        seen.add(name)
        out = set(direct.get(name, ()))
        for c in calls.get(name, ()):
            out |= closure(c, seen, skip)
        return out
    return {name: closure(name) for name in fns}, closure



def mode_funcs(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                pairs = {}
                break
            if isinstance(v, ast.Name):
                pairs[k.value] = v.id
            elif isinstance(v, ast.Lambda):
                names = [n.func.id for n in ast.walk(v.body) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                if not names:
                    pairs = {}
                    break
                pairs[k.value] = names[0]
            else:
                pairs = {}
                break
        if len(pairs) >= 2:
            found.update(pairs)
    return found


def expected(module: str) -> dict[str, set[str]]:
    path = pathlib.Path("src/leftbrain/core") / f"{module}.py"
    (reads, closure), funcs = derive(path), mode_funcs(path)
    handlers = frozenset(funcs.values())
    entry = ENTRY.get(module, module)
    common = closure(entry, skip=handlers)
    modes = list(getattr(importlib.import_module(f"leftbrain.core.{module}"), "MODES", ()))
    return {m: (reads.get(funcs[m], set()) | common) if m in funcs else closure(entry) for m in modes}


@pytest.mark.parametrize("module", MODULES)
def test_the_declared_parameters_are_the_ones_the_code_reads(module):
    declared = importlib.import_module(f"leftbrain.core.{module}").MODE_PARAMS
    for mode, names in expected(module).items():
        assert set(declared[mode]) == names, (
            f"{module}.{mode}: declared {sorted(set(declared[mode]) - names)} that nothing reads, "
            f"missing {sorted(names - set(declared[mode]))} that the code does read"
        )


@pytest.mark.parametrize("module", MODULES)
def test_every_mode_is_declared(module):
    mod = importlib.import_module(f"leftbrain.core.{module}")
    assert set(mod.MODE_PARAMS) == set(mod.MODES)
