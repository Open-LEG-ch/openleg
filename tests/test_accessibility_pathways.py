# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accessibility contract for the four product pathways (#521).

The invoice pages already pin their semantic contract; these tests give the
other pathways the same categories, targeting semantics rather than utility
class names: every interactive control has an accessible name, a visible
keyboard focus style exists, state changes are announced through
status/alert roles, and tables carry scoped headers.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _read(*parts: str) -> str:
    return (TEMPLATES_DIR.joinpath(*parts)).read_text(encoding="utf-8")


def _assert_named_controls(text: str, surface: str):
    """Every button must carry text content; icon-only controls need aria-label."""
    for button in re.findall(r"<button\b[^>]*>(.*?)</button>", text, flags=re.DOTALL):
        stripped = re.sub(r"<[^>]+>", "", button).strip()
        aria_labelled = re.search(r"<button\b[^>]*aria-label", button)
        assert stripped or aria_labelled, f"{surface}: a button has no accessible name"


def _assert_focus_style(text: str, surface: str):
    assert "focus-visible" in text or ":focus" in text, (
        f"{surface}: interactive controls need a visible keyboard focus style"
    )


def test_utility_login_labels_its_field_and_announces_outcomes():
    text = _read("utility", "login.html")
    _assert_focus_style(text, "utility login")
    _assert_named_controls(text, "utility login")
    label = re.search(r"<label\b[^>]*for=\"([^\"]+)\"", text)
    assert label, "the email field label must be programmatically associated"
    assert f'id="{label.group(1)}"' in text, (
        "the labelled field must exist with the label's for target id"
    )
    assert 'id="form-error"' in text and re.search(
        r"<div[^>]*id=\"form-error\"[^>]*role=\"alert\"", text
    ), "login errors must be announced (role=alert)"
    assert re.search(r"<div[^>]*id=\"form-success\"[^>]*role=\"status\"", text), (
        "login success must be announced (role=status)"
    )


def test_utility_register_labels_every_field():
    text = _read("utility", "register.html")
    _assert_focus_style(text, "utility register")
    _assert_named_controls(text, "utility register")
    inputs = re.findall(r"<input\b[^>]*id=\"([^\"]+)\"", text)
    assert inputs, "the register form must name its inputs"
    for input_id in inputs:
        assert re.search(rf"<label\b[^>]*for=\"{input_id}\"", text), (
            f"input #{input_id} has no associated label"
        )


def test_owner_dashboard_announces_state_changes():
    text = _read("dashboard.html")
    _assert_focus_style(text, "owner dashboard")
    _assert_named_controls(text, "owner dashboard")
    assert 'role="progressbar"' in text, (
        "the readiness figure must be a named progressbar, not colour alone"
    )
    assert 'role="status"' in text and 'role="alert"' in text, (
        "saved and error states must be announced"
    )
    assert re.search(r"<span[^>]*id=\"copy-ref-status\"[^>]*role=\"status\"", text), (
        "copying the referral link must be announced, not only shown as text"
    )


def test_operator_dashboard_tables_carry_scoped_headers_and_names():
    text = "\n".join(
        _read(path)
        for path in (
            "leg_workspace_base.html",
            "leg_dashboard.html",
            "leg_workspace/members.html",
            "leg_workspace/correspondence.html",
            "leg_workspace/formation.html",
            "leg_workspace/settings.html",
        )
    )
    _assert_focus_style(text, "operator dashboard")
    _assert_named_controls(text, "operator dashboard")
    for label in re.findall(r"<label\b[^>]*for=\"([^\"]+)\"", text):
        assert f'id="{label}"' in text, f"label targets missing id #{label}"
    for template, count in (("members.html", 2), ("correspondence.html", 1)):
        tables = re.findall(
            r"<table\b[^>]*>(.*?)</table>",
            _read("leg_workspace", template),
            flags=re.DOTALL,
        )
        assert len(tables) >= count, f"{template}: expected at least {count} tables"
        for index, table in enumerate(tables, 1):
            caption = re.search(r"<caption\b[^>]*>(.*?)</caption>", table, re.DOTALL)
            assert caption and caption.group(1).strip(), (
                f"{template}: table {index} needs a caption"
            )
            headers = re.findall(r"<th\b[^>]*>", table)
            assert headers and all('scope="col"' in header for header in headers), (
                f"{template}: table {index} needs scoped column headers"
            )


def test_gemeinde_dashboard_announces_copy_and_names_its_controls():
    text = _read("gemeinde", "dashboard.html")
    _assert_focus_style(text, "gemeinde dashboard")
    _assert_named_controls(text, "gemeinde dashboard")
    for label in re.findall(r"<label\b[^>]*for=\"([^\"]+)\"", text):
        assert f'id="{label}"' in text, f"label targets missing id #{label}"
    assert re.search(
        r"<span[^>]*id=\"copy-invite-status\"[^>]*role=\"status\"", text
    ), "copying the invite link must be announced, not only shown as text"
