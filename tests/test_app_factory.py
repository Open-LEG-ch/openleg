# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application factory contract."""

import subprocess
import sys


def test_import_is_inert_and_factory_instances_are_isolated():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from unittest.mock import patch

with (
    patch('database.is_db_available') as database_probe,
    patch('logging.basicConfig') as logging_probe,
):
    import app

database_probe.assert_not_called()
logging_probe.assert_not_called()
first = app.create_app(
    {'TESTING': True, 'SECRET_KEY': 'first', 'RATELIMIT_STORAGE_URI': 'memory://'},
    load_environment=False,
    check_database=False,
)
second = app.create_app(
    {'TESTING': True, 'SECRET_KEY': 'second', 'RATELIMIT_STORAGE_URI': 'memory://'},
    load_environment=False,
    check_database=False,
)
assert first is not second
assert first.config['SECRET_KEY'] == 'first'
assert second.config['SECRET_KEY'] == 'second'
assert first.test_client().get('/livez').status_code == 200

with patch.object(app, 'create_app', return_value=object()) as factory:
    app._compatibility_app = None
    app.app._get_current_object()

factory.assert_called_once_with()
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
