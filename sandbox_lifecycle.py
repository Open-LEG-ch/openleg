# SPDX-License-Identifier: AGPL-3.0-or-later
"""Own a scratch root across gateway crashes without reviving old villages."""

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

POSTGRES_CONTROL_TIMEOUT_SECONDS = 120


class ScratchOwner:
    """Exclusive root lease and durable identities for disposable processes."""

    def __init__(self, directory, postgres_bin):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.postgres_bin = Path(postgres_bin)
        self.lease = (self.directory / ".owner.lock").open("a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise RuntimeError(
                "a sandbox gateway already owns this directory"
            ) from None
        try:
            self.recover()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def start_token(pid):
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]

    @staticmethod
    def write(path, payload):
        temporary = path.with_suffix(".pending")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(payload, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)

    def record_worker(self, directory, process):
        self.write(
            directory / "worker.json",
            {"pid": process.pid, "start": self.start_token(process.pid)},
        )

    def record_transport(self, directory):
        self.write(self.directory / ".runtime.json", {"transport": str(directory)})

    @staticmethod
    def _running(pid, start):
        """Check that the same live process still holds this PID."""
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[19] == start and fields[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False

    def stop_workspace(self, directory):
        """Stop verified processes in one owned workspace, never a reused PID."""
        directory = Path(directory).resolve()
        if directory.parent != self.directory or not re.fullmatch(
            r"[a-f0-9]{32}", directory.name
        ):
            raise RuntimeError("scratch worker directory is not owned")
        identities = {}
        record = directory / "worker.json"
        if record.exists():
            identity = json.loads(record.read_text())
            pid = int(identity["pid"])
            if pid <= 1:
                raise RuntimeError("invalid scratch worker identity")
            identities[pid] = identity["start"]
        # Popen can succeed before the gateway publishes its journal. Find
        # processes in the exact private workdir, including pre-exec children.
        for candidate in Path("/proc").iterdir():
            try:
                if (
                    not candidate.name.isdecimal()
                    or candidate.stat().st_uid != os.getuid()
                ):
                    continue
                if (candidate / "cwd").resolve(strict=True) == directory:
                    pid = int(candidate.name)
                    identities[pid] = self.start_token(pid)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        for pid, start in identities.items():
            if not self._running(pid, start):
                continue
            try:
                actual_directory = Path(f"/proc/{pid}/cwd").resolve(strict=True)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if pid == os.getpid() or actual_directory != directory:
                raise RuntimeError("scratch worker directory does not match")

            def send(sig, pid=pid, start=start):
                if not self._running(pid, start):
                    return
                try:
                    group = os.getpgid(pid)
                    try:
                        group_directory = Path(f"/proc/{group}/cwd").resolve(
                            strict=True
                        )
                    except FileNotFoundError:
                        group_directory = None
                    own_group = group == pid or group_directory == directory
                    if own_group:
                        os.killpg(group, sig)
                    else:
                        # A pre-exec child must not signal the gateway's group.
                        # Re-evaluate on escalation: it may have since execed.
                        os.kill(pid, sig)
                except (FileNotFoundError, ProcessLookupError):
                    pass

            send(signal.SIGTERM)
            deadline = time.monotonic() + 5
            while self._running(pid, start) and time.monotonic() < deadline:
                time.sleep(0.05)
            if self._running(pid, start):
                send(signal.SIGKILL)
                deadline = time.monotonic() + 5
                while self._running(pid, start) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if self._running(pid, start):
                    raise RuntimeError("scratch worker could not be stopped")

    def _postgres_identity(self, data, pid):
        """Identify an owned postmaster by its data path, executable and start."""
        try:
            process = Path(f"/proc/{pid}")
            if process.stat().st_uid != os.getuid():
                return None
            arguments = (process / "cmdline").read_bytes().split(b"\0")
            if os.fsencode(data) not in arguments or b"-D" not in arguments:
                return None
            executable = Path(os.readlink(process / "exe")).name
            if executable.removesuffix(" (deleted)") != "postgres":
                return None
            if (process / "cwd").resolve(strict=True) != data:
                return None
            start = self.start_token(pid)
            return start if self._running(pid, start) else None
        except (FileNotFoundError, ProcessLookupError):
            return None

    def _stop_unrecorded_postgres(self, data):
        """Stop a verified live cluster before retiring a damaged PID journal."""
        for candidate in Path("/proc").iterdir():
            if not candidate.name.isdecimal():
                continue
            pid = int(candidate.name)
            start = self._postgres_identity(data, pid)
            if start is None or not self._running(pid, start):
                continue
            try:
                os.kill(pid, signal.SIGQUIT)
            except ProcessLookupError:
                continue
            self._wait_postgres(pid, start)

    def _wait_postgres(self, pid, start):
        """Wait for the verified postmaster to exit before deleting its storage."""
        deadline = time.monotonic() + POSTGRES_CONTROL_TIMEOUT_SECONDS
        while self._running(pid, start) and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._running(pid, start):
            raise RuntimeError("scratch PostgreSQL could not be stopped")

    def recover(self):
        """Retire owned processes and scratch storage before accepting villages."""
        # These exact names belong to this runtime. Unrelated files stay intact.
        for directory in self.directory.iterdir():
            if (
                re.fullmatch(r"[a-f0-9]{32}", directory.name)
                and directory.is_dir()
                and not directory.is_symlink()
            ):
                self.stop_workspace(directory)
                shutil.rmtree(directory)
        for directory in self.directory.iterdir():
            if (
                re.fullmatch(r"postgres-[a-f0-9]{12}", directory.name)
                and directory.is_dir()
                and not directory.is_symlink()
            ):
                data = directory / "data"
                pidfile = data / "postmaster.pid"
                stopped = False
                if pidfile.exists():
                    try:
                        pid = int(pidfile.read_text().splitlines()[0])
                    except (FileNotFoundError, IndexError, ValueError):
                        pid = None
                    # A reused PID without this data path belongs to another process.
                    start = (
                        self._postgres_identity(data, pid) if pid and pid > 1 else None
                    )
                    if start is not None:
                        subprocess.run(
                            [
                                str(self.postgres_bin / "pg_ctl"),
                                "-D",
                                str(data),
                                "stop",
                                "-m",
                                "immediate",
                                "-w",
                            ],
                            check=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=POSTGRES_CONTROL_TIMEOUT_SECONDS,
                        )
                        self._wait_postgres(pid, start)
                        stopped = True
                if not stopped:
                    self._stop_unrecorded_postgres(data)
                shutil.rmtree(directory)
        record = self.directory / ".runtime.json"
        if record.exists():
            transport = Path(json.loads(record.read_text())["transport"])
            if (
                transport.parent != Path(tempfile.gettempdir())
                or not re.fullmatch(r"ols-[a-zA-Z0-9_]+", transport.name)
                or transport.is_symlink()
            ):
                raise RuntimeError("invalid scratch transport directory")
            if transport.exists():
                if transport.stat().st_uid != os.getuid():
                    raise RuntimeError("scratch transport owner does not match")
                shutil.rmtree(transport)
            record.unlink()

    def close(self):
        if not self.lease.closed:
            fcntl.flock(self.lease, fcntl.LOCK_UN)
            self.lease.close()
