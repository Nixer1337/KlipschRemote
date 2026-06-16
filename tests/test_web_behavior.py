"""Behavioural parity: run klipsch.js's conversion formulas and compare to Python.

``test_web_parity.py`` checks the formulas *statically* (constants + key tokens).
This goes further: it lifts each arithmetic conversion's source text straight out
of ``web/klipsch.js`` and evaluates it over its whole input range, asserting it
produces byte-identical results to the ``klipsch_ble`` reference.

The JS arithmetic subset these formulas use (``clamp`` / ``Math.trunc`` /
``Math.round`` / ``+ - * /``) is also valid Python syntax, so the real JS
expression can be evaluated directly against a tiny ``Math``/``clamp`` shim — no
Node, no JS engine, runs in the plain pytest CI. A formula that picks up an
operator-precedence slip, a trunc/round swap or an off-by-one clamp — none of
which the token check would catch — fails here.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from klipsch_ble import constants as c

WEB_JS = Path(__file__).resolve().parent.parent / "web" / "klipsch.js"
SRC = WEB_JS.read_text(encoding="utf-8")


class _Math:
    """The slice of JS's global ``Math`` the conversion formulas reference."""

    trunc = staticmethod(math.trunc)
    max = staticmethod(max)
    min = staticmethod(min)

    @staticmethod
    def round(x: float) -> int:
        # JS Math.round is round-half-up (toward +Inf), not Python's banker's round.
        return math.floor(x + 0.5)


def _clamp(v: float, lo: float, hi: float) -> int:
    return max(lo, min(hi, math.trunc(v)))


# Names the formulas reference, bound to the Python source-of-truth values. Their
# equality with the JS-side constants is what test_web_parity.py separately guards.
_NS = {
    "Math": _Math,
    "clamp": _clamp,
    "MAX_VOLUME_RAW": c.MAX_VOLUME_RAW,
    "EQ_MIN": c.EQ_MIN,
    "EQ_MAX": c.EQ_MAX,
    "EQ_OFFSET": c.EQ_OFFSET,
    "SUB_RAW_MIN": c.SUB_RAW_MIN,
    "SUB_RAW_MAX": c.SUB_RAW_MAX,
    "SUB_DB_OFFSET": c.SUB_DB_OFFSET,
}


def _js_formula(name: str) -> tuple[str, str]:
    """Extract ``const <name> = (<param>) => <expr>;`` -> (param, expr)."""
    m = re.search(rf"\b{name}\s*=\s*\((\w+)\)\s*=>\s*([^;]+);", SRC)
    assert m, f"could not find arrow function `{name}` in {WEB_JS.name}"
    return m.group(1), m.group(2).strip()


def _eval_js(name: str, value: int) -> int:
    """Evaluate klipsch.js's `name` formula at `value` in the Math/clamp shim."""
    param, expr = _js_formula(name)
    # The expression can only see the sandboxed namespace below (no builtins),
    # and the source is our own repo file parsed down to one arithmetic line.
    return eval(expr, {"__builtins__": {}}, {**_NS, param: value})


# (js name, python reference, input domain). Every conversion clamps out-of-range
# input on both sides, so the domains sweep past the valid bounds to exercise that
# shared clamping — and to catch a clamp present on only one side.
_CASES = [
    ("volumePercentToRaw", c.volume_percent_to_raw, range(-10, 111)),
    ("volumeRawToPercent", c.volume_raw_to_percent, range(-5, c.MAX_VOLUME_RAW + 6)),
    ("volumeRawToDb", c.volume_raw_to_db, range(-5, c.MAX_VOLUME_RAW + 6)),
    ("eqLevelToByte", c.eq_level_to_byte, range(-15, 12)),
    ("eqByteToLevel", c.eq_byte_to_level, range(-4, 21)),
    ("subRawToDb", c.sub_raw_to_db, range(-5, 40)),
    ("subDbToRaw", c.sub_db_to_raw, range(-30, 20)),
]


@pytest.mark.parametrize("name, ref, domain", _CASES, ids=[case[0] for case in _CASES])
def test_js_conversion_matches_python(name, ref, domain):
    for x in domain:
        assert _eval_js(name, x) == ref(x), f"{name}({x}) diverges: JS vs Python"
