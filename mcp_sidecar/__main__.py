# SPDX-License-Identifier: AGPL-3.0-or-later
import os

import uvicorn

from mcp_sidecar.server import create_app

dev_static = os.environ.get("OPENLEG_MCP_DEV_STATIC") == "true"
host = os.environ.get("OPENLEG_MCP_BIND_HOST", "127.0.0.1" if dev_static else "0.0.0.0")
# The static-token transport is a local tracer and never listens beyond loopback.
if dev_static and host not in {"127.0.0.1", "::1", "localhost"}:
    raise SystemExit("OPENLEG_MCP_DEV_STATIC requires a loopback bind host")

uvicorn.run(
    create_app(),
    host=host,
    port=8000,
    access_log=False,
    proxy_headers=False,
)
