# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep native test instrumentation when fixtures isolate application settings."""

import os


def isolated_environment(settings):
    controls = {
        key: os.environ[key]
        for key in ("MUTANT_UNDER_TEST", "MUTMUT_DEPENDENCY_DEPTH")
        if key in os.environ
    }
    return {**controls, **settings}
