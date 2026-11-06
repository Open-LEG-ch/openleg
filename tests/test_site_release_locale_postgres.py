# SPDX-License-Identifier: AGPL-3.0-or-later
"""Expand native constraints without rewriting existing recipient preferences."""

import psycopg2
import pytest
from flask import Flask

from tests import test_recipient_locale_postgres

interest_database = test_recipient_locale_postgres.interest_database
save_profile = test_recipient_locale_postgres.save_profile
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("locale", ["en", "it_CH"])
def test_release_migration_accepts_new_recipient_language_and_never_narrows(
    interest_database, locale
):
    db = interest_database
    app = Flask(__name__)
    app.config["SITE_RELEASE_ENABLED"] = True
    with app.app_context():
        db.create_tables()
        assert save_profile(db, preferred_locale=locale)
    db.create_tables()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale FROM buildings")
        assert cur.fetchone()["preferred_locale"] == locale
    with pytest.raises(psycopg2.IntegrityError):
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE buildings SET preferred_locale='../../invalid'")
