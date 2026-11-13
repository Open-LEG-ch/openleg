# SPDX-License-Identifier: AGPL-3.0-or-later
import os

import uvicorn

from mcp_sidecar.server import create_app

uvicorn.run(
    create_app(),
    host=os.environ.get("OPENLEG_MCP_BIND_HOST", "127.0.0.1"),
    port=8000,
    access_log=False,
    proxy_headers=False,
)
