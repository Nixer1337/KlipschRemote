"""Guard: the Web Bluetooth port (``web/klipsch.js``) must mirror the Python protocol.

``web/klipsch.js`` is a hand-maintained re-implementation of
``klipsch_ble/constants.py`` + ``klipsch_ble/models.py``. There is no build step
linking the two, so the only thing stopping them from drifting apart — a UUID
changed on one side, an input or model added to desktop but not web, an offset
flipped — is this test.

It parses the *static protocol surface* straight out of the JS source (no Node
needed, so it runs in the existing pytest-only CI) and asserts it equals the
Python source of truth. When something drifts, the failing assertion names the
exact field. If a refactor of ``klipsch.js`` changes its shape enough that the
parser below can't find a value, update the parser here in lockstep — that
breakage is the point: it forces a human to re-confirm the two stayed in sync.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from klipsch_ble import constants as c
from klipsch_ble import models as m

ROOT = Path(__file__).resolve().parent.parent
WEB_JS = ROOT / "web" / "klipsch.js"
THEME_PY = ROOT / "klipsch_remote" / "theme.py"
SRC = WEB_JS.read_text(encoding="utf-8")


# ---- tiny JS readers --------------------------------------------------------
def _object_body(name: str) -> str:
    """Return the text inside ``const <name> = { ... }`` (brace-balanced)."""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\{{", SRC)
    assert match, f"could not find object literal `{name}` in {WEB_JS.name}"
    start = match.end() - 1  # index of the opening '{'
    depth = 0
    for j in range(start, len(SRC)):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[start + 1 : j]
    raise AssertionError(f"unbalanced braces parsing `{name}` in {WEB_JS.name}")


def _js_number(name: str) -> int:
    """Read a primitive ``<name> = <int>`` (decimal or 0x...) literal."""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(-?0x[0-9a-fA-F]+|-?\d+)\b", SRC)
    assert match, f"could not find numeric const `{name}` in {WEB_JS.name}"
    return int(match.group(1), 0)


def _expr(name: str) -> str:
    """Return the arrow-function body of ``const <name> = (args) => <expr>;``,
    whitespace-collapsed, for structural (formula) checks."""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\([^)]*\)\s*=>\s*([^;]+);", SRC)
    assert match, f"could not find arrow function `{name}` in {WEB_JS.name}"
    return re.sub(r"\s+", " ", match.group(1)).strip()


# ---- UUIDs ------------------------------------------------------------------
def _js_uuids() -> dict[str, str]:
    """Every CH_*/SVC_* UUID defined in klipsch.js, name -> full lowercase UUID.

    Builds the ``da6d0f<short>-<SFX>`` shorthands exactly as the JS does (and
    asserts the prefix + suffix it uses match Python's), plus the full-string
    standard-DIS UUIDs.
    """
    sfx = re.search(r'\bSFX\s*=\s*"([0-9a-f-]+)"', SRC)
    assert sfx, "could not find the SFX UUID suffix in klipsch.js"
    assert sfx.group(1) == c._SFX, "Klipsch UUID suffix differs between JS and Python"

    prefix = re.search(r"u\s*=\s*\(short\)\s*=>\s*`([0-9a-z]+)\$\{short\}-\$\{SFX\}`", SRC)
    assert prefix, "could not find the `u(short)` UUID builder in klipsch.js"
    assert prefix.group(1) == "da6d0f", "Klipsch UUID prefix differs between JS and Python"

    uuids: dict[str, str] = {}
    for name, short in re.findall(
        r"\bconst\s+((?:CH|SVC)_\w+)\s*=\s*u\(\"([0-9a-fA-F]+)\"\)", SRC
    ):
        uuids[name] = f"{prefix.group(1)}{short}-{sfx.group(1)}"
    for name, full in re.findall(
        r"\bconst\s+((?:CH|SVC)_\w+)\s*=\s*\"([0-9a-fA-F-]{36})\"", SRC
    ):
        uuids[name] = full
    return uuids


def _py_uuids() -> dict[str, str]:
    return {n: getattr(c, n) for n in dir(c) if n.startswith(("CH_", "SVC_"))}


def test_uuid_names_match():
    """Neither side has a characteristic/service the other is missing."""
    assert set(_js_uuids()) == set(_py_uuids()), (
        "the set of GATT UUID names differs between klipsch.js and constants.py — "
        "a characteristic was added/removed on only one side"
    )


def test_uuid_values_match():
    js, py = _js_uuids(), _py_uuids()
    for name in py:
        assert js[name] == py[name].lower(), f"UUID {name} differs between JS and Python"


def test_char_to_service_map_matches():
    body = _object_body("CHAR_TO_SERVICE")
    js_uuids = _js_uuids()
    js_map = {
        js_uuids[char]: js_uuids[svc]
        for char, svc in re.findall(r"\[(\w+)\]\s*:\s*(\w+)", body)
    }
    py_map = {char.lower(): svc.lower() for char, svc in c.CHAR_TO_SERVICE.items()}
    assert js_map == py_map, "CHAR_TO_SERVICE differs between klipsch.js and constants.py"


# ---- numeric protocol constants --------------------------------------------
def test_numeric_constants_match():
    assert _js_number("MAX_VOLUME_RAW") == c.MAX_VOLUME_RAW
    assert _js_number("EQ_MIN") == c.EQ_MIN
    assert _js_number("EQ_MAX") == c.EQ_MAX
    assert _js_number("EQ_OFFSET") == c.EQ_OFFSET
    assert _js_number("SUB_CHANNEL") == c.SUB_CHANNEL
    assert _js_number("SUB_RAW_MIN") == c.SUB_RAW_MIN
    assert _js_number("SUB_RAW_MAX") == c.SUB_RAW_MAX
    assert _js_number("SUB_DB_OFFSET") == c.SUB_DB_OFFSET
    assert _js_number("SUB_LEVEL_BYTE_INDEX") == c.SUB_LEVEL_BYTE_INDEX


def test_eq_channels_map_matches():
    body = _object_body("EQ_CHANNELS")
    js_uuids = _js_uuids()
    js_map = {
        ch: js_uuids[const] for ch, const in re.findall(r"(\w+)\s*:\s*(CH_\w+)", body)
    }
    py_map = {ch: uuid.lower() for ch, uuid in c.EQ_CHANNELS.items()}
    assert js_map == py_map, "EQ_CHANNELS differs between klipsch.js and constants.py"


# ---- inputs -----------------------------------------------------------------
def test_input_enum_matches():
    body = _object_body("Input")
    js_input = {name: int(v) for name, v in re.findall(r"(\w+)\s*:\s*(\d+)", body)}
    py_input = {member.name: member.value for member in c.Input}
    assert js_input == py_input, "Input enum differs between klipsch.js and constants.py"


def test_input_names_match():
    body = _object_body("INPUT_NAMES")
    js_names = {int(v): name for v, name in re.findall(r'(\d+)\s*:\s*"(\w+)"', body)}
    py_names = {member.value: name for member, name in c.INPUT_NAMES.items()}
    assert js_names == py_names, "INPUT_NAMES differs between klipsch.js and constants.py"


def test_input_aliases_match():
    body = _object_body("INPUT_ALIASES")
    js_aliases = {alias: int(v) for alias, v in re.findall(r"(\w+)\s*:\s*(\d+)", body)}
    py_aliases = {alias: member.value for alias, member in c.INPUT_ALIASES.items()}
    assert js_aliases == py_aliases, (
        "INPUT_ALIASES differs between klipsch.js and constants.py"
    )


# ---- model identification ---------------------------------------------------
def test_model_by_number_matches():
    body = _object_body("MODEL_BY_NUMBER")
    js_map = dict(re.findall(r'"(\d+)"\s*:\s*"(\w+)"', body))
    py_map = {num: model.value for num, model in m.MODEL_BY_NUMBER.items()}
    assert js_map == py_map, "MODEL_BY_NUMBER differs between klipsch.js and models.py"


def test_model_by_hw_rev_matches():
    body = _object_body("MODEL_BY_HW_REV")
    js_map = {int(rev): name for rev, name in re.findall(r'(\d+)\s*:\s*"(\w+)"', body)}
    py_map = {rev: model.value for rev, model in m.MODEL_BY_HW_REV.items()}
    assert js_map == py_map, "MODEL_BY_HW_REV differs between klipsch.js and models.py"


def test_model_display_names_match():
    body = _object_body("MODELS")
    js_display = dict(re.findall(r'(\w+)\s*:\s*\{\s*display\s*:\s*"([^"]+)"\s*\}', body))
    py_display = {model.value: info.display_name for model, info in m.MODEL_INFO.items()}
    assert js_display == py_display, (
        "model display names differ between klipsch.js and models.py"
    )


# ---- value-conversion formulas ---------------------------------------------
# Constants are checked above; this catches a sign/operator swap in a formula
# that equal constants alone wouldn't (e.g. a + offset becoming a - offset).
# Tokens are deliberately short fragments so harmless reformatting won't trip it.
@pytest.mark.parametrize(
    "func, must_contain",
    [
        ("volumePercentToRaw", ["* MAX_VOLUME_RAW", "/ 100"]),
        ("volumeRawToPercent", ["* 100", "/ MAX_VOLUME_RAW"]),
        ("volumeRawToDb", ["-80 +", "88 / MAX_VOLUME_RAW"]),
        ("eqLevelToByte", ["+ EQ_OFFSET"]),
        ("eqByteToLevel", ["- EQ_OFFSET"]),
        ("subRawToDb", ["- SUB_DB_OFFSET"]),
        ("subDbToRaw", ["+ SUB_DB_OFFSET"]),
    ],
)
def test_conversion_formula_structure(func, must_contain):
    body = _expr(func)
    for token in must_contain:
        assert token in body, (
            f"{func} in klipsch.js no longer contains `{token}` — its formula may "
            f"have drifted from constants.py; re-verify the conversion by hand"
        )


# ---- EQ presets: web vs desktop ---------------------------------------------
# The web remote reproduces the desktop app's EQ presets (klipsch_remote/theme.py).
# theme.py imports Flet, so read its values via AST instead of importing it — that
# keeps this test runnable in the Flet-free pytest CI.
def _theme_eq_presets() -> dict[str, tuple[int, ...]]:
    tree = ast.parse(THEME_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # theme.py uses an annotated assignment: `EQ_PRESETS: dict[...] = {...}`.
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "EQ_PRESETS" and node.value:
            return {k: tuple(v) for k, v in ast.literal_eval(node.value).items()}
    raise AssertionError("EQ_PRESETS not found in theme.py")


def test_eq_presets_match_desktop():
    body = _object_body("EQ_PRESETS")
    js = {
        name: tuple(int(v) for v in vals.split(","))
        for name, vals in re.findall(r"(\w+)\s*:\s*\[([-\d,\s]+)\]", body)
    }
    assert js == _theme_eq_presets(), (
        "EQ presets differ between klipsch.js and klipsch_remote/theme.py"
    )
