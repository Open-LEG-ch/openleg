# SPDX-License-Identifier: AGPL-3.0-or-later
import uvicorn

from mcp_sidecar.server import create_app

uvicorn.run(create_app(), host="0.0.0.0", port=8000, access_log=False)
