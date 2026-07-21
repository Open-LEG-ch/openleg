# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-host on-ramp routes (Program 9).

Serves the one-command installer verbatim from ``scripts/install.sh`` so the
piped-to-shell command can never drift from the audited file a cautious host
reads first. The ``/self-host`` landing page joins this blueprint in a later
slice.
"""

import os

from flask import Blueprint, Response

self_host_bp = Blueprint("self_host", __name__)

_INSTALLER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scripts", "install.sh"
)


@self_host_bp.route("/install.sh")
def installer_script():
    """Return the installer bytes verbatim (text/x-shellscript)."""
    with open(_INSTALLER_PATH, "rb") as handle:
        return Response(handle.read(), mimetype="text/x-shellscript")
