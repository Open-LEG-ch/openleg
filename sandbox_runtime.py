# SPDX-License-Identifier: AGPL-3.0-or-later
"""Own disposable village processes; product requests never select a database."""

import atexit
import hmac
import http.client
import logging
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg2
from flask import Flask, Response, abort, redirect, render_template, request, session
from psycopg2 import sql
from psycopg2.extensions import make_dsn

import i18n
from app_config import MAX_UPLOAD_BYTES, validated_public_site_url

SOURCE = Path(__file__).resolve().parent
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def require_private_directory(directory):
    status = directory.stat()
    if status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise RuntimeError(
            f"sandbox directory must be private and owned by the runner: {directory}"
        )
    for parent in directory.parents:
        mode = parent.stat().st_mode
        if mode & 0o022 and not mode & stat.S_ISVTX:
            raise RuntimeError(f"sandbox directory has a writable parent: {parent}")


class LocalPostgres:
    """A password-protected, Unix-socket-only PostgreSQL 16 scratch cluster."""

    def __init__(self, directory, socket_directory, postgres_bin):
        self.directory = Path(directory)
        self.socket_directory = Path(socket_directory)
        self.bin = Path(postgres_bin)
        version = subprocess.check_output(
            [str(self.bin / "postgres"), "--version"], text=True
        )
        if not version.startswith("postgres (PostgreSQL) 16."):
            raise ValueError("the sandbox requires PostgreSQL 16")
        self.directory.mkdir(mode=0o700)
        self.socket_directory.mkdir(mode=0o700)
        self.password = secrets.token_urlsafe(32)
        self.data = self.directory / "data"
        with (self.directory / "init.log").open("w") as output:
            subprocess.run(
                [
                    str(self.bin / "initdb"),
                    "-D",
                    str(self.data),
                    "--username=sandbox_admin",
                    "--pwfile=/dev/stdin",
                    "--auth=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                    "--no-sync",
                ],
                input=(self.password + "\n").encode(),
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
        try:
            subprocess.run(
                [
                    str(self.bin / "pg_ctl"),
                    "-D",
                    str(self.data),
                    "-l",
                    str(self.directory / "postgres.log"),
                    "-o",
                    f"-k {self.socket_directory} -h '' -c unix_socket_permissions=0700",
                    "start",
                    "-w",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            self.dsn = make_dsn(
                dbname="postgres",
                user="sandbox_admin",
                password=self.password,
                host=str(self.socket_directory),
                connect_timeout=5,
            )
            with psycopg2.connect(self.dsn) as connection:
                connection.autocommit = True
                with connection.cursor() as cursor:
                    for name in ("postgres", "template1"):
                        cursor.execute(
                            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                                sql.Identifier(name)
                            )
                        )
        except BaseException:
            self.close()
            raise

    def close(self):
        if (self.data / "postmaster.pid").exists():
            subprocess.run(
                [
                    str(self.bin / "pg_ctl"),
                    "-D",
                    str(self.data),
                    "stop",
                    "-m",
                    "immediate",
                    "-w",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.close()


class Villages:
    """A bounded owner of scratch storage, processes and opaque workspace IDs."""

    def __init__(
        self,
        directory,
        *,
        postgres_bin,
        max_workspaces,
        lifetime_seconds,
        max_upload_bytes=2 * 1024 * 1024,
    ):
        self.max_upload_bytes = int(max_upload_bytes)
        if not 65536 <= self.max_upload_bytes <= MAX_UPLOAD_BYTES:
            raise ValueError("sandbox upload bound must be between 64 KiB and 10 MiB")
        # Fail before provisioning scratch services. The gateway itself must
        # retain TCP for its public listener, so install the probe in a child.
        try:
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from sandbox_network import deny_internet; deny_internet()",
                ],
                cwd=SOURCE,
                env={"PATH": os.defpath, "PYTHONPATH": str(SOURCE)},
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("sandbox network filter unavailable") from error
        if probe.returncode != 0:
            raise RuntimeError("sandbox network filter unavailable")
        self.directory = Path(directory).resolve()
        self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private_directory(self.directory)
        self.transport = tempfile.TemporaryDirectory(prefix="ols-")
        self.max_workspaces = max(1, min(int(max_workspaces), 20))
        self.lifetime_seconds = max(60, min(int(lifetime_seconds), 86400))
        self.lock = threading.RLock()
        self.workspaces = {}
        self.closed = False
        self.postgres = LocalPostgres(
            self.directory / ("postgres-" + secrets.token_hex(6)),
            Path(self.transport.name) / "pg",
            postgres_bin,
        )
        atexit.register(self.close)

    def create(self, origin, locale="de_CH", *, owner=None):
        self.reap()
        with self.lock:
            if self.closed:
                raise RuntimeError("sandbox is closed")
            existing = next(
                (
                    workspace
                    for workspace in self.workspaces.values()
                    if owner and workspace.get("owner") == owner
                ),
                None,
            )
            if existing is None:
                if len(self.workspaces) >= self.max_workspaces:
                    raise OverflowError("workspace capacity reached")
                identifier = secrets.token_hex(16)
                directory = self.directory / identifier
                directory.mkdir(mode=0o700)
                workspace = {
                    "id": identifier,
                    "name": "ols_" + identifier,
                    "directory": directory,
                    "socket": Path(self.transport.name) / (identifier + ".sock"),
                    "created": time.monotonic(),
                    "process": None,
                    "operation": threading.RLock(),
                    "provisioned": threading.Event(),
                    "ready": False,
                    "owner": owner,
                }
                self.workspaces[identifier] = workspace
        if existing:
            if not existing["provisioned"].wait(timeout=35):
                raise RuntimeError("village startup did not finish")
            with self.lock:
                if (
                    existing["ready"]
                    and self.workspaces.get(existing["id"]) is existing
                ):
                    return existing
            raise RuntimeError("village startup failed")
        try:
            with workspace["operation"]:
                self._provision(workspace, origin, locale)
                with self.lock:
                    if self.closed:
                        raise RuntimeError("sandbox is closed")
                    workspace["ready"] = True
            return workspace
        except BaseException:
            self.destroy(workspace["id"])
            raise
        finally:
            workspace["provisioned"].set()

    def _provision(self, workspace, origin, locale):
        identifier, name = workspace["id"], workspace["name"]
        directory, socket_path = workspace["directory"], workspace["socket"]
        password = secrets.token_urlsafe(32)
        connection = psycopg2.connect(self.postgres.dsn)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(
                        sql.Identifier(name)
                    ),
                    (password,),
                )
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(name), sql.Identifier(name)
                    )
                )
                cursor.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(name)
                    )
                )
            worker_dsn = make_dsn(
                dbname=name,
                user=name,
                password=password,
                host=str(self.postgres.socket_directory),
                connect_timeout=5,
            )
            # Deliberately do not inherit service credentials or configuration.
            env = {
                "PATH": os.defpath,
                "PYTHONPATH": str(SOURCE),
                "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8",
                "TMPDIR": str(directory),
                "DATABASE_URL": worker_dsn,
                "OPENLEG_SANDBOX_WORKER": "1",
                "OPENLEG_SANDBOX_DIRECTORY": str(directory),
                "OPENLEG_SANDBOX_WORKSPACE": identifier,
                "OPENLEG_SANDBOX_MAX_UPLOAD_BYTES": str(self.max_upload_bytes),
                "SECRET_KEY": secrets.token_urlsafe(48),
                "APP_BASE_URL": origin,
                "PUBLIC_SITE_URL": origin,
                "BABEL_DEFAULT_LOCALE": i18n.normalize_locale(locale) or "de_CH",
                "REDIS_URL": "memory://",
                "DB_POOL_MIN": "1",
                "DB_POOL_MAX": "4",
            }
            with (directory / "worker.log").open("wb") as log:
                workspace["process"] = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "gunicorn",
                        "sandbox_worker:application",
                        "--bind",
                        "unix:" + str(socket_path),
                        "--workers",
                        "1",
                        "--threads",
                        "2",
                        "--timeout",
                        "45",
                        "--no-control-socket",
                        "--chdir",
                        str(directory),
                    ],
                    env=env,
                    cwd=directory,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if workspace["process"].poll() is not None:
                    raise RuntimeError("village worker could not start")
                if socket_path.exists():
                    try:
                        with UnixConnection(socket_path) as channel:
                            channel.request(
                                "GET",
                                "/livez",
                                headers={"X-Forwarded-Proto": urlsplit(origin).scheme},
                            )
                            if channel.getresponse().status == 200:
                                return
                    except (OSError, http.client.HTTPException):
                        pass
                time.sleep(0.05)
            raise RuntimeError("village worker readiness timed out")
        finally:
            connection.close()

    def get(self, identifier):
        with self.lock:
            workspace = self.workspaces.get(identifier)
            if workspace and not workspace["ready"]:
                return None
            if (
                workspace
                and time.monotonic() - workspace["created"] < self.lifetime_seconds
            ):
                return workspace
        if workspace:
            try:
                self.destroy(identifier)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch expired cleanup failed: %s", type(error).__name__
                )
        return None

    def reap(self):
        with self.lock:
            now = time.monotonic()
            identifiers = [
                identifier
                for identifier, workspace in self.workspaces.items()
                if workspace["provisioned"].is_set()
                and (
                    not workspace["ready"]
                    or now - workspace["created"] >= self.lifetime_seconds
                )
            ]
        for identifier in identifiers:
            try:
                self.destroy(identifier)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch workspace cleanup failed: %s", type(error).__name__
                )

    def destroy(self, identifier):
        with self.lock:
            workspace = self.workspaces.get(identifier)
        if not workspace:
            return
        with workspace["operation"]:
            with self.lock:
                if self.workspaces.get(identifier) is not workspace:
                    return
            process = workspace["process"]
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            connection = psycopg2.connect(self.postgres.dsn)
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(workspace["name"])
                        )
                    )
                    cursor.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(workspace["name"])
                        )
                    )
            finally:
                connection.close()
            workspace["socket"].unlink(missing_ok=True)
            shutil.rmtree(workspace["directory"])
            with self.lock:
                self.workspaces.pop(identifier, None)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            identifiers = list(self.workspaces)
        for identifier in identifiers:
            try:
                self.destroy(identifier)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch workspace cleanup failed: %s", type(error).__name__
                )
        try:
            self.postgres.close()
            for workspace in list(self.workspaces.values()):
                process = workspace["process"]
                if process and process.poll() is None:
                    raise RuntimeError("scratch worker could not be stopped")
                shutil.rmtree(workspace["directory"], ignore_errors=True)
            self.workspaces.clear()
            shutil.rmtree(self.postgres.directory)
            self.transport.cleanup()
        except BaseException:
            # Retain identities for another shutdown attempt when teardown fails.
            with self.lock:
                self.closed = False
            raise
        atexit.unregister(self.close)


def create_gateway(
    directory,
    *,
    postgres_bin=None,
    max_workspaces=4,
    lifetime_seconds=3600,
    max_upload_bytes=2 * 1024 * 1024,
    origin="http://localhost",
):
    """Create the standalone sandbox entry point, never a production app mode."""
    origin = validated_public_site_url(origin)
    expected_host = urlsplit(origin).netloc
    villages = Villages(
        directory,
        postgres_bin=postgres_bin
        or os.environ.get("OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"),
        max_workspaces=max_workspaces,
        lifetime_seconds=lifetime_seconds,
        max_upload_bytes=max_upload_bytes,
    )
    web = Flask(__name__)
    web.config.update(
        SECRET_KEY=secrets.token_urlsafe(48),
        SESSION_COOKIE_NAME="openleg_sandbox",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=origin.startswith("https://"),
        MAX_CONTENT_LENGTH=villages.max_upload_bytes,
    )
    web.extensions["villages"] = villages
    i18n.init_app(web)

    @web.context_processor
    def private_templates():
        return {"private_page": True}

    @web.before_request
    def csrf():
        if request.host != expected_host:
            abort(400)
        if request.path == "/sandbox/start":
            expected = session.get("csrf", "")
            submitted = request.form.get("csrf_token", "")
            if (
                not expected
                or not submitted.isascii()
                or not hmac.compare_digest(expected, submitted)
            ):
                abort(400)

    @web.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; frame-src 'none'; "
            "form-action 'self'; base-uri 'self'; object-src 'none'"
        )
        return response

    @web.get("/sandbox")
    @web.get("/")
    def index():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        return render_template(
            "sandbox/entry.html",
            csrf_token=session["csrf"],
            lifetime_minutes=villages.lifetime_seconds // 60,
        )

    @web.post("/sandbox/start")
    def start():
        if request.form.get("persona") != "resident":
            abort(400)
        if villages.get(session.get("workspace")):
            return redirect("/sandbox/home")
        try:
            workspace = villages.create(
                origin, session.get("locale", "de_CH"), owner=session["csrf"]
            )
        except OverflowError:
            return render_template(
                "sandbox/entry.html",
                csrf_token=session["csrf"],
                capacity=True,
                lifetime_minutes=villages.lifetime_seconds // 60,
            ), 503
        except (RuntimeError, OSError, psycopg2.OperationalError):
            return render_template(
                "sandbox/entry.html",
                csrf_token=session["csrf"],
                unavailable=True,
                lifetime_minutes=villages.lifetime_seconds // 60,
            ), 503
        session["workspace"] = workspace["id"]
        return redirect("/sandbox/enter")

    @web.route(
        "/<path:path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    def product(path):
        workspace = villages.get(session.get("workspace"))
        if not workspace:
            return redirect("/sandbox", code=303)
        headers = {
            key: value
            for key, value in request.headers
            if key.lower() not in HOP_HEADERS
            and not key.lower().startswith("x-forwarded-")
        }
        cookies = [
            part
            for part in headers.get("Cookie", "").split(";")
            if not part.strip().startswith("openleg_sandbox=")
        ]
        headers["Cookie"] = ";".join(cookies)
        headers["Host"] = "localhost"
        headers["X-Forwarded-Proto"] = urlsplit(origin).scheme
        target = "/" + quote(path, safe="/:@!$&'()*+,;=-._~")
        if request.query_string:
            target += "?" + request.query_string.decode("latin-1")
        try:
            with UnixConnection(workspace["socket"]) as channel:
                channel.request(
                    request.method, target, body=request.get_data(), headers=headers
                )
                upstream = channel.getresponse()
                return Response(
                    upstream.read(),
                    status=upstream.status,
                    headers=[
                        (key, value)
                        for key, value in upstream.getheaders()
                        if key.lower() not in HOP_HEADERS
                    ],
                )
        except (OSError, http.client.HTTPException):
            return "Das Übungsdorf ist nicht verfügbar. Bitte starten Sie neu.", 503

    return web
