# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict request options shared by operator write routes."""


def parse_dry_run(query, payload):
    """Accept one exact query boolean; never accept this option in JSON."""
    values = query.getlist("dry_run")
    if "dry_run" in payload or (values and values not in (["true"], ["false"])):
        raise ValueError("Invalid dry_run option")
    return values == ["true"]
