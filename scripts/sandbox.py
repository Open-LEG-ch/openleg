#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run a bounded, disposable Sonnwil sandbox without production services."""

import argparse
import json
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox_runtime import create_gateway


def process_start(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def stop(directory):
    record = json.loads((directory / "runner.json").read_text())
    pid = record["pid"]
    if pid <= 1 or process_start(pid) != record["start"]:
        raise RuntimeError("sandbox runner identity does not match")
    os.kill(pid, signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".scratch/sandbox")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--max-workspaces", type=int, default=4)
    parser.add_argument("--max-upload-mib", type=int, default=2)
    parser.add_argument(
        "--stop",
        action="store_true",
        help="stop this scratch runtime and clean its villages",
    )
    parser.add_argument("--lifetime-seconds", type=int, default=3600)
    parser.add_argument(
        "--origin", help="public origin; defaults to the local listening URL"
    )
    parser.add_argument(
        "--postgres-bin",
        default=os.environ.get(
            "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
        ),
    )
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if args.stop:
        stop(directory)
        return
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = directory / "runner.json"
    descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"pid": os.getpid(), "start": process_start(os.getpid())}, output)

    def shutdown(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    web = None
    try:
        web = create_gateway(
            directory,
            postgres_bin=args.postgres_bin,
            max_workspaces=args.max_workspaces,
            max_upload_bytes=args.max_upload_mib * 1024 * 1024,
            lifetime_seconds=args.lifetime_seconds,
            origin=args.origin or f"http://127.0.0.1:{args.port}",
        )
        web.run(host="127.0.0.1", port=args.port, use_reloader=False, threaded=True)
    finally:
        if web:
            web.extensions["villages"].close()
        record.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
