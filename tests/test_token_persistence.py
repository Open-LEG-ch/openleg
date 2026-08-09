# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for token JSON persistence."""

import json

import token_persistence


def test_first_save_persists_empty_created_at_mapping(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setattr(token_persistence, "_get_token_file_path", lambda: str(path))

    assert token_persistence.save_tokens({}, {}) is True
    assert json.loads(path.read_text(encoding="utf-8"))["created_at"] == {}
    assert token_persistence.load_tokens() == ({}, {}, {}, {})
