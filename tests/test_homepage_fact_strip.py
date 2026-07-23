# SPDX-License-Identifier: AGPL-3.0-or-later
"""Homepage fact strip (the redesigned "Proof Bar").

Four facts of different kinds rendered as a hairline measurement strip rather
than four identical boxed cards, so the quantifiable facts read as mono
numerals and the row no longer mirrors the stakeholder card grid above it.
"""

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _strip():
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(r'aria-labelledby="fakten-title".*?</section>', html, re.S)
    assert match, "fact strip section not found"
    return match.group(0)


def test_fact_strip_uses_description_list_not_boxed_cards():
    strip = _strip()
    assert "<dl" in strip and "<dt" in strip and "<dd" in strip
    # No card chrome: the old rounded/shadow tiles must be gone.
    assert "rounded-xl border" not in strip
    assert "shadow" not in strip


def test_fact_strip_renders_quantifiable_facts_as_mono_numerals():
    strip = _strip()
    for figure in ("2026", "bis 40", "3 "):
        assert re.search(
            r"font-mono tabular-nums[^>]*>\s*" + re.escape(figure), strip
        ), figure


def test_fact_strip_keeps_legal_basis_citation():
    strip = _strip()
    assert "Art. 17d/17e Stromversorgungsgesetz" in strip
    assert "Art. 19e-19h Stromversorgungsverordnung" in strip
    assert "Art. 8a decies Abs. 6 StromVV" in strip
