# SPDX-License-Identifier: AGPL-3.0-or-later
"""Own disposable village processes; product requests never select a database."""

import atexit
import hmac
import http.client
import logging
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg2
from babel.units import format_unit
from flask import Flask, Response, abort, redirect, render_template, request, session
from flask_babel import get_locale
from psycopg2 import sql
from psycopg2.extensions import make_dsn

import i18n
from app_config import MAX_UPLOAD_BYTES, validated_public_site_url
from sandbox_lifecycle import POSTGRES_CONTROL_TIMEOUT_SECONDS, ScratchOwner
from sandbox_seed import ACCOUNTS, SEED_VERSION
from sandbox_urls import scope_page, scope_url

SOURCE = Path(__file__).resolve().parent
POSTGRES_SETUP_TIMEOUT_SECONDS = 60
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
    "x-openleg-initial-persona",
}


class RetirementConflict(Exception):
    """Another lifecycle action owns or retired this workspace generation."""


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
            [str(self.bin / "postgres"), "--version"],
            text=True,
            timeout=POSTGRES_SETUP_TIMEOUT_SECONDS,
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
                timeout=POSTGRES_SETUP_TIMEOUT_SECONDS,
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
                timeout=POSTGRES_CONTROL_TIMEOUT_SECONDS,
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
                timeout=POSTGRES_CONTROL_TIMEOUT_SECONDS,
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
        inactivity_seconds=900,
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
        self.owner = ScratchOwner(self.directory, postgres_bin)
        self.transport = tempfile.TemporaryDirectory(prefix="ols-")
        self.owner.record_transport(self.transport.name)
        self.max_workspaces = max(1, min(int(max_workspaces), 20))
        self.lifetime_seconds = max(1, min(int(lifetime_seconds), 86400))
        self.inactivity_seconds = max(1, min(int(inactivity_seconds), 86400))
        self.lock = threading.RLock()
        self.workspaces = {}
        self.retiring = {}
        self.replacements = set()
        self.retirement_claims = set()
        self.stop_reaper = threading.Event()
        self.closed = False
        try:
            self.postgres = LocalPostgres(
                self.directory / ("postgres-" + secrets.token_hex(6)),
                Path(self.transport.name) / "pg",
                postgres_bin,
            )
        except BaseException:
            try:
                self.owner.recover()
            finally:
                self.transport.cleanup()
                self.owner.close()
            raise
        atexit.register(self.close)
        self.reaper = threading.Thread(
            target=self._reap_loop, name="sandbox-cleanup", daemon=True
        )
        self.reaper.start()

    def create(
        self,
        origin,
        locale="de_CH",
        *,
        owner=None,
        replacement=None,
        initial_persona="resident",
    ):
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
                occupied = set(self.workspaces) | set(self.retiring) | self.replacements
                if replacement in self.replacements:
                    occupied.discard(replacement)
                if len(occupied) >= self.max_workspaces:
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
                    "last_active": time.monotonic(),
                    "attempts": 0,
                    "retry_at": 0,
                    "process": None,
                    "operation": threading.RLock(),
                    "provisioned": threading.Event(),
                    "ready": False,
                    "owner": owner,
                    "initial_persona": initial_persona,
                }
                self.workspaces[identifier] = workspace
                # The new in-progress workspace now owns this allocation.
                self.replacements.discard(replacement)
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
                    workspace["last_active"] = time.monotonic()
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
                "OPENLEG_SANDBOX_PREFIX": "/v/" + identifier,
                "OPENLEG_SANDBOX_MAX_UPLOAD_BYTES": str(self.max_upload_bytes),
                "SECRET_KEY": secrets.token_urlsafe(48),
                "APP_BASE_URL": origin + "/v/" + identifier,
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
                        "--pid",
                        str(directory / "worker.pid"),
                        "--chdir",
                        str(directory),
                    ],
                    env=env,
                    cwd=directory,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            self.owner.record_worker(directory, workspace["process"])
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

    def _expired(self, workspace):
        now = time.monotonic()
        return workspace["ready"] and (
            now - workspace["created"] >= self.lifetime_seconds
            or now - workspace["last_active"] >= self.inactivity_seconds
        )

    def get(self, identifier):
        with self.lock:
            workspace = self.workspaces.get(identifier)
            if workspace and not workspace["ready"]:
                return None
            if workspace and not self._expired(workspace):
                workspace["last_active"] = time.monotonic()
                return workspace
        if workspace:
            try:
                self.destroy(identifier)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch expired cleanup failed: %s", type(error).__name__
                )
        return None

    @contextmanager
    def use(self, identifier):
        workspace = self.get(identifier)
        if not workspace:
            yield None
            return
        with workspace["operation"]:
            with self.lock:
                current = (
                    self.workspaces.get(identifier) is workspace and not self.closed
                )
            yield workspace if current else None

    def reap(self):
        with self.lock:
            expired = [
                key
                for key, workspace in self.workspaces.items()
                if workspace["provisioned"].is_set()
                and (not workspace["ready"] or self._expired(workspace))
            ]
            pending = [
                key
                for key, workspace in self.retiring.items()
                if workspace["attempts"] < 3
                and workspace["retry_at"] <= time.monotonic()
            ]
        for identifiers, retry in ((expired, False), (pending, True)):
            for identifier in identifiers:
                try:
                    self.destroy(identifier, retry=retry)
                except Exception as error:
                    logging.getLogger(__name__).warning(
                        "Scratch workspace cleanup failed: %s", type(error).__name__
                    )

    def _reap_loop(self):
        while not self.stop_reaper.wait(0.5):
            try:
                self.reap()
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch cleanup failed: %s", type(error).__name__
                )

    def _record_cleanup(self):
        try:
            self.owner.write(
                self.directory / ".cleanup.json",
                [
                    {
                        "id": key,
                        "attempts": workspace["attempts"],
                        "error": workspace.get("error"),
                    }
                    for key, workspace in self.retiring.items()
                ],
            )
        except OSError as error:
            # The process log remains the failure record when the disk cannot
            # accept the journal. Retirement and finite retries must continue.
            logging.getLogger(__name__).warning(
                "Scratch cleanup journal failed: %s", type(error).__name__
            )

    @contextmanager
    def _claim_retirement(self, identifier, *, replacement=False):
        """Claim one browser lifecycle action without blocking in-flight work."""
        with self.lock:
            if (
                identifier not in self.workspaces
                or identifier in self.retirement_claims
            ):
                raise RetirementConflict(identifier)
            self.retirement_claims.add(identifier)
            if replacement:
                self.replacements.add(identifier)
        try:
            yield
        finally:
            with self.lock:
                self.retirement_claims.discard(identifier)
                if replacement:
                    self.replacements.discard(identifier)

    def reset(self, identifier, origin, locale, owner):
        with self._claim_retirement(identifier, replacement=True):
            if not self.destroy(identifier):
                return None
            return self.create(origin, locale, owner=owner, replacement=identifier)

    def discard(self, identifier):
        """Discard once; a conflicting late response must not rewrite a cookie."""
        with self._claim_retirement(identifier):
            return self.destroy(identifier)

    def destroy(self, identifier, *, retry=False):
        with self.lock:
            workspace = self.workspaces.pop(identifier, None) or self.retiring.get(
                identifier
            )
            if not workspace:
                return True
            self.retiring[identifier] = workspace
        with workspace["operation"]:
            with self.lock:
                if identifier not in self.retiring:
                    return True
                if workspace["attempts"] >= 3:
                    return False
                if retry and workspace["retry_at"] > time.monotonic():
                    return False
                workspace["retry_at"] = float("inf")
                workspace["attempts"] += 1
                self._record_cleanup()
            try:
                if not workspace.get("worker_stopped"):
                    self.owner.stop_workspace(workspace["directory"])
                    process = workspace["process"]
                    if process:
                        process.wait(timeout=5)
                    workspace["worker_stopped"] = True
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
                shutil.rmtree(workspace["directory"], ignore_errors=False)
            except Exception as error:
                with self.lock:
                    workspace["error"] = type(error).__name__
                    workspace["retry_at"] = (
                        time.monotonic() + 2 ** workspace["attempts"]
                    )
                    self._record_cleanup()
                logging.getLogger(__name__).warning(
                    "Village retirement failed: %s", type(error).__name__
                )
                return False
            with self.lock:
                self.retiring.pop(identifier, None)
                self._record_cleanup()
            return True

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stop_reaper.set()
            identifiers = list(self.workspaces) + list(self.retiring)
        if threading.current_thread() is not self.reaper:
            self.reaper.join(timeout=10)
        for identifier in identifiers:
            try:
                self.destroy(identifier)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Scratch shutdown failed: %s", type(error).__name__
                )
        try:
            # Journal or database failures must not strand a later worker.
            for workspace in list(self.workspaces.values()) + list(
                self.retiring.values()
            ):
                self.owner.stop_workspace(workspace["directory"])
                process = workspace["process"]
                if process and process.poll() is None:
                    process.wait(timeout=5)
            self.postgres.close()
            for workspace in self.retiring.values():
                if workspace["directory"].exists():
                    shutil.rmtree(workspace["directory"])
            shutil.rmtree(self.postgres.directory)
            self.workspaces.clear()
            self.retiring.clear()
            self.transport.cleanup()
            (self.directory / ".runtime.json").unlink(missing_ok=True)
            (self.directory / ".cleanup.json").unlink(missing_ok=True)
            self.owner.close()
        except BaseException:
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
    inactivity_seconds=900,
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
        inactivity_seconds=inactivity_seconds,
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
        def duration(seconds):
            for unit, factor in (("hour", 3600), ("minute", 60), ("second", 1)):
                if seconds % factor == 0:
                    return format_unit(
                        seconds // factor, "duration-" + unit, locale=get_locale()
                    )

        return {
            "private_page": True,
            "accounts": ACCOUNTS,
            "inactivity_timeout": duration(villages.inactivity_seconds),
            "maximum_lifetime": duration(villages.lifetime_seconds),
        }

    @web.before_request
    def csrf():
        if request.host != expected_host:
            abort(400)
        if request.path == "/sandbox/start" or request.path.endswith(
            ("/sandbox/reset", "/sandbox/discard")
        ):
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
            lifetime_seconds=villages.lifetime_seconds,
            inactivity_seconds=villages.inactivity_seconds,
        )

    def startup_unavailable(*, capacity=False):
        return render_template(
            "sandbox/entry.html",
            csrf_token=session["csrf"],
            capacity=capacity,
            unavailable=not capacity,
        ), 503

    @web.post("/sandbox/start")
    def start():
        selected_persona = request.form.get("persona")
        if selected_persona not in ACCOUNTS:
            abort(400)
        if villages.get(session.get("workspace")):
            return redirect("/v/" + session["workspace"] + "/sandbox/home")
        try:
            workspace = villages.create(
                origin,
                session.get("locale", "de_CH"),
                owner=session["csrf"],
                initial_persona=selected_persona,
            )
        except OverflowError:
            return startup_unavailable(capacity=True)
        except (RuntimeError, OSError, psycopg2.OperationalError):
            return startup_unavailable()
        session["workspace"] = workspace["id"]
        return redirect("/v/" + workspace["id"] + "/sandbox/enter")

    def expired():
        return render_template("sandbox/expired.html"), 410

    def owned(identifier):
        return session.get("workspace") == identifier and villages.get(identifier)

    @web.get("/v/<identifier>/sandbox/manage")
    def manage(identifier):
        if not owned(identifier):
            return expired()
        return render_template(
            "sandbox/manage.html",
            prefix="/v/" + identifier,
            csrf_token=session["csrf"],
            seed_version=SEED_VERSION,
            lifetime_seconds=villages.lifetime_seconds,
            inactivity_seconds=villages.inactivity_seconds,
        )

    @web.post("/v/<identifier>/sandbox/reset")
    @web.post("/v/<identifier>/sandbox/discard")
    def retire(identifier):
        if not owned(identifier):
            return expired()
        reset = request.path.endswith("/reset")
        if request.form.get("confirm") != ("reset" if reset else "discard"):
            abort(400)
        if reset and request.form.get("seed_version") != SEED_VERSION:
            abort(400)
        if reset:
            try:
                workspace = villages.reset(
                    identifier, origin, session.get("locale", "de_CH"), session["csrf"]
                )
            except RetirementConflict:
                abort(409)
            except OverflowError:
                session.pop("workspace", None)
                return startup_unavailable(capacity=True)
            except (RuntimeError, OSError, psycopg2.OperationalError):
                session.pop("workspace", None)
                return startup_unavailable()
            if not workspace:
                session.pop("workspace", None)
                return render_template("sandbox/expired.html", cleanup_failed=True), 503
            session["workspace"] = workspace["id"]
            return redirect("/v/" + workspace["id"] + "/sandbox/enter")
        try:
            discarded = villages.discard(identifier)
        except RetirementConflict:
            abort(409)
        if not discarded:
            session.pop("workspace", None)
            return render_template("sandbox/expired.html", cleanup_failed=True), 503
        session.pop("workspace", None)
        return redirect("/sandbox", code=303)

    @web.route(
        "/<path:path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    def unscoped(path):
        if session.get("workspace"):
            return expired()
        return redirect("/sandbox", code=303)

    @web.route(
        "/v/<identifier>/",
        defaults={"path": ""},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    @web.route(
        "/v/<identifier>/<path:path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    def product(identifier, path):
        if session.get("workspace") != identifier:
            return expired()
        # Reading an incomplete upload must not hold the village's retirement
        # lock. Recheck ownership/lifetime after the request body arrives.
        body = request.get_data()
        with villages.use(identifier) as workspace:
            if not workspace:
                return expired()
            return proxy(workspace, path, body)

    def proxy(workspace, path, body):
        prefix = "/v/" + workspace["id"]
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
        headers["X-Forwarded-Prefix"] = prefix
        # Only the gateway's explicit start POST grants one initial login.
        # A stale enter URL or a browser-supplied header cannot restore a role.
        if request.method == "GET" and path == "sandbox/enter":
            initial_persona = workspace.pop("initial_persona", None)
            if initial_persona:
                headers["X-OpenLEG-Initial-Persona"] = initial_persona
        target = "/" + quote(path, safe="/:@!$&'()*+,;=-._~")
        if request.query_string:
            target += "?" + quote(request.query_string, safe="%&=+;:@/?!$'()*,-._~[]")
        try:
            with UnixConnection(workspace["socket"]) as channel:
                channel.request(request.method, target, body=body, headers=headers)
                upstream = channel.getresponse()
                body = upstream.read()
                response_headers = [
                    (key, value)
                    for key, value in upstream.getheaders()
                    if key.lower() not in HOP_HEADERS
                ]
                content_type = upstream.getheader("Content-Type", "")
                if content_type.startswith("text/html"):
                    body = scope_page(body.decode("utf-8"), prefix, origin).encode(
                        "utf-8"
                    )
                response_headers = [
                    (
                        key,
                        scope_url(value, prefix, origin)
                        if key.lower() == "location"
                        else value,
                    )
                    for key, value in response_headers
                ]
                return Response(body, status=upstream.status, headers=response_headers)

        except (OSError, http.client.HTTPException):
            return "Das Übungsdorf ist nicht verfügbar. Bitte starten Sie neu.", 503

    return web
