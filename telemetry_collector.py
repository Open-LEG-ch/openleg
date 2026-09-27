# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local normalized-sample spool. No device discovery, commands or inbound port.

Run `python telemetry_collector.py CONFIG enqueue < sample.json` or `... flush`.
Scheduling and hardware adapters belong to the local operator.
"""

import argparse
import fcntl
import ipaddress
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import telemetry

MAX_QUEUE = 10000
MAX_SAMPLE_BYTES = 8192
MAX_AGE = 86400
MAX_ATTEMPTS = 8


def configuration(payload):
    required = {"installation_id", "source", "spool", "endpoint", "allowed_endpoints"}
    if (
        not isinstance(payload, dict)
        or not required <= payload.keys()
        or payload.keys() - required - {"credential", "last_sequence"}
    ):
        raise ValueError("invalid_collector_configuration")
    result = dict(payload)
    result["installation_id"] = str(uuid.UUID(payload["installation_id"]))
    source = telemetry.enrollment(payload["source"])
    result["source"] = source
    telemetry.integer(payload.get("last_sequence", 0), 0, 2**63 - 2)
    if (
        not isinstance(payload["spool"], str)
        or not Path(payload["spool"]).is_absolute()
    ):
        raise ValueError("absolute_spool_path_required")
    endpoint = payload["endpoint"]
    allowlist = payload["allowed_endpoints"]
    if not isinstance(allowlist, list) or any(
        not isinstance(v, str) for v in allowlist
    ):
        raise ValueError("invalid_endpoint_allowlist")
    if endpoint is None:
        return result
    if (
        not isinstance(endpoint, str)
        or endpoint not in allowlist
        or any(c.isspace() for c in endpoint)
    ):
        raise ValueError("endpoint_not_allowlisted")
    parsed = urlsplit(endpoint)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_ingestion_endpoint")
    path = (
        r"/api/telemetry/v1/communities/[A-Za-z0-9_.:-]{1,64}/installations/"
        + re.escape(result["installation_id"])
        + "/samples"
    )
    if not re.fullmatch(path, parsed.path):
        raise ValueError("invalid_ingestion_endpoint")
    if parsed.scheme != "https":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if parsed.scheme != "http" or not loopback:
            raise ValueError("https_required")
    token = payload.get("credential")
    if not isinstance(token, str) or not re.fullmatch(r"olt_[A-Za-z0-9_-]{43}", token):
        raise ValueError("invalid_collector_credential")
    return result


def load_configuration(path):
    with open(path, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ValueError("configuration_must_be_private")
        raw = handle.read(telemetry.MAX_BYTES + 1)
        if len(raw) > telemetry.MAX_BYTES:
            raise ValueError("configuration_too_large")
        return configuration(json.loads(raw))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(endpoint, token, body):
    """Fixed POST, TLS verification, no redirects or environment proxy routing."""
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(
            request, timeout=10
        ) as response:
            return response.status, response.read(4096)
    except HTTPError as exc:
        return exc.code, b""
    except (URLError, TimeoutError, OSError):
        return 0, b""


class Collector:
    def __init__(self, config, *, send=_send, clock=time.time):
        self.config = configuration(config)
        self.send = send
        self.clock = clock
        spool = Path(self.config["spool"])
        # A private directory also protects SQLite journal sidecars.
        spool.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = spool.parent.stat()
        if info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("spool_directory_must_be_private")
        fd = os.open(spool, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != os.getuid()
            ):
                raise ValueError("spool_must_be_private")
        finally:
            os.close(fd)
        self.db = sqlite3.connect(spool, timeout=5)
        self.db.execute(
            "PRAGMA max_page_count=8192"
        )  # At most 32 MiB at default 4 KiB pages.
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL,
                sequence INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS samples (
                sequence INTEGER PRIMARY KEY, queued_at REAL NOT NULL, body TEXT NOT NULL
            );
        """)
        identity = json.dumps(
            {
                "installation_id": self.config["installation_id"],
                **{
                    k: self.config["source"][k]
                    for k in ("device_id", "source_id", "cadence_seconds")
                },
            },
            sort_keys=True,
        )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO state(id,identity,sequence) VALUES(1,?,?)",
                (identity, self.config.get("last_sequence", 0)),
            )
            if self.db.execute("SELECT identity FROM state").fetchone()[0] != identity:
                raise ValueError("spool_identity_mismatch")

    def close(self):
        self.db.close()

    def _expire(self):
        removed = self.db.execute(
            "DELETE FROM samples WHERE queued_at<?", (self.clock() - MAX_AGE,)
        ).rowcount
        # The server permits owners to shorten retention to one day. Drop old
        # observations before batching, with a margin for the HTTP timeout.
        cutoff = self.clock() - MAX_AGE + 30
        stale = []
        for sequence, body in self.db.execute("SELECT sequence,body FROM samples"):
            observed = json.loads(body)["observed_at"]
            if (
                observed is not None
                and telemetry.timestamp(observed).timestamp() < cutoff
            ):
                stale.append((sequence,))
        self.db.executemany("DELETE FROM samples WHERE sequence=?", stale)
        return removed + len(stale)

    def enqueue(self, sample):
        """Return assigned sequence. Preserve explicit null observation timestamps."""
        if (
            not isinstance(sample, dict)
            or {"sample_id", "sequence", "device_id", "source_id", "cadence_seconds"}
            & sample.keys()
        ):
            raise ValueError("normalized_sample_required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._expire()
            if (
                self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                >= MAX_QUEUE
            ):
                raise ValueError("collector_queue_full")
            sequence = self.db.execute("SELECT sequence FROM state").fetchone()[0] + 1
            row = {
                **sample,
                "sample_id": str(uuid.uuid4()),
                "sequence": sequence,
                **{
                    k: self.config["source"][k]
                    for k in ("device_id", "source_id", "cadence_seconds")
                },
            }
            row = telemetry.observation(
                row,
                {**self.config["source"], "raw_retention_days": 1},
                datetime.fromtimestamp(self.clock(), timezone.utc),
            )
            row.pop("fingerprint")
            body = json.dumps(row, separators=(",", ":"))
            if len(body.encode()) > MAX_SAMPLE_BYTES:
                raise ValueError("sample_too_large")
            self.db.execute(
                "INSERT INTO samples VALUES(?,?,?)", (sequence, self.clock(), body)
            )
            self.db.execute("UPDATE state SET sequence=?", (sequence,))
        return sequence

    def resume(self):
        """Explicit local action after correcting credentials or a rejected batch."""
        with self.db:
            self.db.execute("UPDATE state SET blocked=0,attempts=0,next_attempt=0")

    def flush_once(self):
        # Only flushers contend on this lock. Enqueue never waits on network I/O.
        fd = os.open(
            self.config["spool"] + ".flush.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"state": "busy"}
            return self._flush_locked()

    def _flush_locked(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            expired = self._expire()
            if self.config["endpoint"] is None:
                return {"state": "local_only", "expired": expired}
            attempts, due, blocked = self.db.execute(
                "SELECT attempts,next_attempt,blocked FROM state"
            ).fetchone()
            if blocked or self.clock() < due:
                return {
                    "state": "blocked" if blocked else "backoff",
                    "expired": expired,
                }
            batch = []
            sequences = []
            for sequence, raw in self.db.execute(
                "SELECT sequence,body FROM samples ORDER BY sequence LIMIT ?",
                (telemetry.MAX_BATCH,),
            ):
                candidate = [*batch, json.loads(raw)]
                body = json.dumps(
                    {"schema_version": telemetry.VERSION, "samples": candidate},
                    separators=(",", ":"),
                ).encode()
                if len(body) > telemetry.MAX_BYTES:
                    break
                batch, sequences = candidate, [*sequences, sequence]
            if not batch:
                return {"state": "empty", "expired": expired}
        body = json.dumps(
            {"schema_version": telemetry.VERSION, "samples": batch},
            separators=(",", ":"),
        ).encode()
        status, raw = self.send(
            self.config["endpoint"], self.config["credential"], body
        )
        accepted = False
        if status in (200, 201):
            try:
                ack = json.loads(raw)
                accepted = (
                    isinstance(ack, dict)
                    and ack.get("schema_version") == telemetry.VERSION
                    and all(
                        type(ack.get(k)) is int and ack[k] >= 0
                        for k in ("accepted", "duplicates")
                    )
                    and ack["accepted"] + ack["duplicates"] == len(batch)
                )
            except (ValueError, UnicodeError):
                pass
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if accepted:
                self.db.execute(
                    "DELETE FROM samples WHERE sequence<=?", (sequences[-1],)
                )
                self.db.execute("UPDATE state SET attempts=0,next_attempt=0")
                return {"state": "sent", "count": len(batch), "expired": expired}
            attempts += 1
            blocked = attempts >= MAX_ATTEMPTS or (
                status not in (0, 200, 201, 408, 429) and status < 500
            )
            self.db.execute(
                "UPDATE state SET attempts=?,next_attempt=?,blocked=?",
                (attempts, self.clock() + min(300, 2**attempts), int(blocked)),
            )
            return {"state": "blocked" if blocked else "retry", "expired": expired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration")
    parser.add_argument("action", choices=("enqueue", "flush", "resume"))
    args = parser.parse_args()
    collector = None
    try:
        collector = Collector(load_configuration(args.configuration))
        if args.action == "enqueue":
            import sys

            raw = sys.stdin.buffer.read(MAX_SAMPLE_BYTES + 1)
            if len(raw) > MAX_SAMPLE_BYTES:
                raise ValueError()
            collector.enqueue(json.loads(raw))
            result = {"state": "queued"}
        elif args.action == "resume":
            collector.resume()
            result = {"state": "resumed"}
        else:
            result = collector.flush_once()
        print(json.dumps(result))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error):
        # No exception details: device samples and credentials stay local.
        print('{"error":"collector_operation_failed"}')
        return 1
    finally:
        if collector is not None:
            collector.close()


if __name__ == "__main__":
    raise SystemExit(main())
