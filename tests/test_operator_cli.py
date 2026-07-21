# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the no-devops operator CLI (Program 9 W3).

`scripts/openleg` wraps the compose lifecycle a non-technical LEG host needs, so
they never have to memorise docker flags. The command->action mapping is pinned
statically here; a real backup/restore round-trip needs Docker and is a manual
verification step.
"""

import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "scripts" / "openleg"


def _content():
    return CLI.read_text(encoding="utf-8")


def test_cli_exists_and_executable():
    assert CLI.is_file()
    assert CLI.stat().st_mode & stat.S_IXUSR


def test_strict_mode():
    assert "set -euo pipefail" in _content()


def test_has_all_subcommands():
    content = _content()
    for sub in ("status", "logs", "update", "backup", "restore", "stop"):
        assert sub in content


def test_wraps_compose():
    assert "docker compose" in _content()


def test_backup_uses_pg_dump_and_restore_uses_psql():
    content = _content()
    assert "pg_dump" in content
    assert "psql" in content


def test_update_restarts_stack():
    assert "up -d" in _content()


def test_unknown_command_shows_usage_and_exits_nonzero():
    content = _content()
    assert "usage" in content.lower()
    assert "exit 2" in content
