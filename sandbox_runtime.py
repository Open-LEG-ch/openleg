# SPDX-License-Identifier: AGPL-3.0-or-later
"""Own disposable village processes; product requests never select a database."""

import atexit
import hmac
import http.client
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
from flask import Flask, Response, abort, redirect, render_template, request, session
from psycopg2 import sql
from psycopg2.extensions import make_dsn

import i18n
from app_config import validated_public_site_url

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
        password_file = self.directory / "password"
        password_file.write_text(self.password)
        password_file.chmod(0o600)
        self.data = self.directory / "data"
        shared = self.bin.parents[3] / "share/postgresql/16"
        with (self.directory / "init.log").open("w") as output:
            subprocess.run(
                [
                    str(self.bin / "initdb"),
                    "-D",
                    str(self.data),
                    "-L",
                    str(shared),
                    "--username=sandbox_admin",
                    f"--pwfile={password_file}",
                    "--auth=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                    "--no-sync",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
        password_file.unlink()
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
        except BaseException:
            self.close()
            raise
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
                check=False,
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

    def __init__(self, directory, *, postgres_bin, max_workspaces, lifetime_seconds):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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

    def create(self, origin, locale="de_CH"):
        with self.lock:
            self.reap()
            if len(self.workspaces) >= self.max_workspaces:
                raise OverflowError("workspace capacity reached")
            identifier = secrets.token_hex(16)
            name = "ols_" + identifier
            password = secrets.token_urlsafe(32)
            directory = self.directory / identifier
            directory.mkdir(mode=0o700)
            socket_path = Path(self.transport.name) / (identifier + ".sock")
            workspace = {
                "id": identifier,
                "name": name,
                "directory": directory,
                "socket": socket_path,
                "created": time.monotonic(),
                "process": None,
            }
            self.workspaces[identifier] = workspace
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
                    "DATABASE_URL": worker_dsn,
                    "OPENLEG_SANDBOX_WORKER": "1",
                    "OPENLEG_SANDBOX_DIRECTORY": str(directory),
                    "OPENLEG_SANDBOX_WORKSPACE": identifier,
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
                                channel.request("GET", "/livez")
                                if channel.getresponse().status == 200:
                                    return workspace
                        except (OSError, http.client.HTTPException):
                            pass
                    time.sleep(0.05)
                raise RuntimeError("village worker readiness timed out")
            except BaseException:
                self.destroy(identifier)
                raise
            finally:
                connection.close()

    def get(self, identifier):
        with self.lock:
            workspace = self.workspaces.get(identifier)
            if (
                workspace
                and time.monotonic() - workspace["created"] < self.lifetime_seconds
            ):
                return workspace
            if workspace:
                self.destroy(identifier)
            return None

    def reap(self):
        for identifier in list(self.workspaces):
            self.get(identifier)

    def destroy(self, identifier):
        with self.lock:
            workspace = self.workspaces.get(identifier)
            if not workspace:
                return
            process = workspace["process"]
            if process:
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
            del self.workspaces[identifier]

    def close(self):
        with self.lock:
            if self.closed:
                return
            for identifier in list(self.workspaces):
                self.destroy(identifier)
            self.postgres.close()
            shutil.rmtree(self.postgres.directory)
            self.transport.cleanup()
            self.closed = True
            atexit.unregister(self.close)


def create_gateway(
    directory,
    *,
    postgres_bin=None,
    max_workspaces=4,
    lifetime_seconds=3600,
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
    )
    web = Flask(__name__)
    web.config.update(
        SECRET_KEY=secrets.token_urlsafe(48),
        SESSION_COOKIE_NAME="openleg_sandbox",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=origin.startswith("https://"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    web.extensions["villages"] = villages
    i18n.init_app(web)

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
            workspace = villages.create(origin, session.get("locale", "de_CH"))
        except OverflowError:
            return render_template(
                "sandbox/entry.html", csrf_token=session["csrf"], capacity=True
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
        target = "/" + path
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
