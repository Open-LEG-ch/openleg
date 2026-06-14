# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-domain storage repositories sharing the database connection seam.

Each module here owns one domain's persistence code. Functions resolve the
connection seam via ``database.get_connection`` at call time, so existing
test monkeypatches on ``database.get_connection`` keep working and callers
that use ``import database as db; db.<func>()`` are unaffected.
"""
