# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swisseldex Datahub SDAT retrieval over FTP with explicit TLS (ftpes://).

Pulls the SDAT files a VNB drops into our Datahub outbox at
``datahub.swisseldex.ch`` and stores them under a local directory. Parsing and
ingestion stay in :mod:`meter_data`; this module only moves bytes.

Credentials come from the environment (see ``.env.example``) and never appear
in logs or reprs. Downloaded files hold real citizen metering data, so the
default local directory sits under the gitignored ``data/`` tree.

Manual run: ``python scripts/fetch_sdat.py``
"""

import ftplib
import logging
import os
import ssl
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_HOST = "datahub.swisseldex.ch"
DEFAULT_PORT = 21
DEFAULT_REMOTE_DIR = "/"
DEFAULT_LOCAL_DIR = "data/sdat"
DEFAULT_TIMEOUT = 60
MAX_RECURSION_DEPTH = 8

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Datahub credentials or connection settings are missing."""


class TransferError(RuntimeError):
    """A download cannot be proven complete."""


@dataclass(repr=False)
class DatahubConfig:
    """Connection settings for the Swisseldex Datahub FTPS endpoint."""

    user: str
    password: str = field(repr=False)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    remote_dir: str = DEFAULT_REMOTE_DIR
    local_dir: str = DEFAULT_LOCAL_DIR
    timeout: int = DEFAULT_TIMEOUT
    passive: bool = True
    verify_tls: bool = True
    ca_bundle: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial, but keeps secrets out
        return (
            f"DatahubConfig(host={self.host!r}, port={self.port!r}, "
            f"remote_dir={self.remote_dir!r}, local_dir={self.local_dir!r}, "
            f"password=***)"
        )


@dataclass
class RemoteFile:
    """One file listed in the Datahub outbox.

    ``path`` is the location relative to the configured remote directory and
    equals ``name`` for a flat outbox. Subdirectories are mirrored under the
    local download directory.
    """

    name: str
    size: int = 0
    modified: datetime | None = None
    path: str = ""

    def __post_init__(self) -> None:
        if self.modified is not None and self.modified.utcoffset() is None:
            self.modified = self.modified.replace(tzinfo=timezone.utc)
        if not self.path:
            self.path = self.name


def _flag(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _int(env: dict[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        logger.warning("[SDAT] %s ist keine Zahl, nutze %s", key, default)
        return default


def load_config(env: dict[str, str] | None = None) -> DatahubConfig:
    """Build a config from environment variables.

    Raises:
        ConfigError: if user or password is missing.
    """
    env = dict(os.environ if env is None else env)

    user = (env.get("SWISSELDEX_FTPS_USER") or "").strip()
    password = env.get("SWISSELDEX_FTPS_PASSWORD") or ""
    missing = [
        key
        for key, value in (
            ("SWISSELDEX_FTPS_USER", user),
            ("SWISSELDEX_FTPS_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "Datahub-Zugangsdaten fehlen: "
            + ", ".join(missing)
            + ". Bitte in .env eintragen (Vorlage: .env.example)."
        )

    return DatahubConfig(
        user=user,
        password=password,
        host=(env.get("SWISSELDEX_FTPS_HOST") or DEFAULT_HOST).strip(),
        port=_int(env, "SWISSELDEX_FTPS_PORT", DEFAULT_PORT),
        remote_dir=(
            env.get("SWISSELDEX_FTPS_REMOTE_DIR") or DEFAULT_REMOTE_DIR
        ).strip(),
        local_dir=(env.get("SWISSELDEX_SDAT_DIR") or DEFAULT_LOCAL_DIR).strip(),
        timeout=_int(env, "SWISSELDEX_FTPS_TIMEOUT", DEFAULT_TIMEOUT),
        passive=_flag(env, "SWISSELDEX_FTPS_PASSIVE", True),
        verify_tls=_flag(env, "SWISSELDEX_FTPS_VERIFY_TLS", True),
        ca_bundle=(env.get("SWISSELDEX_FTPS_CA_BUNDLE") or "").strip() or None,
    )


class _SessionReusingFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS that resumes the control-channel TLS session on data channels.

    Many managed FTPS endpoints (the Datahub included) require the data
    connection to resume the control connection's TLS session and drop
    transfers otherwise. Python's stdlib does not do this on its own.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = getattr(self.sock, "session", None)
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=session
            )
        return conn, size


def _build_ssl_context(config: DatahubConfig) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=config.ca_bundle)
    if not config.verify_tls:
        logger.warning(
            "[SDAT] TLS-Prüfung ist deaktiviert (SWISSELDEX_FTPS_VERIFY_TLS=false). "
            "Nur für Tests gegen ein privates Zertifikat nutzen."
        )
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def connect(config: DatahubConfig) -> ftplib.FTP_TLS:
    """Open an authenticated FTPS session with an encrypted data channel."""
    client = _SessionReusingFTP_TLS(context=_build_ssl_context(config))
    client.connect(host=config.host, port=config.port, timeout=config.timeout)
    client.auth()
    client.login(user=config.user, passwd=config.password)
    client.prot_p()
    client.set_pasv(config.passive)
    logger.info("[SDAT] Verbunden mit %s:%s", config.host, config.port)
    return client


def _strip_dir_prefix(raw: str, remote_dir: str | None) -> str:
    """Drop the listed directory prefix some servers prepend in NLST output."""
    name = raw.strip()
    for prefix in filter(
        None, (remote_dir, remote_dir and remote_dir.rstrip("/"), ".")
    ):
        marker = prefix if prefix.endswith("/") else prefix + "/"
        if name.startswith(marker):
            return name[len(marker) :]
    return name


def _is_safe_name(name: str) -> bool:
    """Reject anything that could write outside the download directory."""
    if not name or name in (".", ".."):
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    return ".." not in name


def _is_safe_relpath(path: str) -> bool:
    """Every segment of a remote path must be a safe file or folder name."""
    segments = path.split("/")
    return bool(segments) and all(_is_safe_name(segment) for segment in segments)


def _parse_mlsd_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # MDTM und die MLSD-Fact "modify" liefern UTC (RFC 3659). Ohne explizite
    # tzinfo vergleicht sich der Wert später gegen eine lokale Zeit und das
    # Fenster von --since-days verschiebt sich um den Offset.
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M%S.%f"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _list_via_nlst(client, remote_dir: str | None) -> list[RemoteFile]:
    entries = []
    for raw in client.nlst():
        name = _strip_dir_prefix(raw, remote_dir)
        if not _is_safe_name(name):
            logger.warning("[SDAT] Eintrag übersprungen (unsicherer Name): %r", raw)
            continue
        size = 0
        modified = None
        try:
            size = int(client.size(name) or 0)
        except (*ftplib.all_errors, ValueError, TypeError):
            pass
        try:
            modified = _parse_mlsd_time(client.sendcmd(f"MDTM {name}").split()[-1])
        except (*ftplib.all_errors, IndexError):
            pass
        entries.append(RemoteFile(name=name, size=size, modified=modified))
    return entries


def _walk_mlsd(
    client, base: str = "", *, recursive: bool = False, depth: int = 0
) -> list[RemoteFile]:
    """List one directory via MLSD, optionally descending into subfolders."""
    entries: list[RemoteFile] = []
    subdirs: list[str] = []

    for name, facts in client.mlsd(path=base or ".", facts=["type", "size", "modify"]):
        kind = facts.get("type", "file")
        if kind in ("cdir", "pdir"):
            continue
        if not _is_safe_name(name):
            logger.warning("[SDAT] Eintrag übersprungen (unsicherer Name): %r", name)
            continue
        relative = f"{base}/{name}" if base else name
        if kind == "dir":
            subdirs.append(relative)
            continue
        if kind != "file":
            continue
        try:
            size = int(facts.get("size") or 0)
        except ValueError:
            size = 0
        entries.append(
            RemoteFile(
                name=name,
                size=size,
                modified=_parse_mlsd_time(facts.get("modify")),
                path=relative,
            )
        )

    if recursive and depth < MAX_RECURSION_DEPTH:
        for subdir in subdirs:
            try:
                entries.extend(
                    _walk_mlsd(client, subdir, recursive=True, depth=depth + 1)
                )
            except ftplib.all_errors as exc:
                # One unreadable folder must not cost us the rest of the outbox.
                logger.warning("[SDAT] Verzeichnis %s nicht lesbar: %s", subdir, exc)
    elif subdirs and recursive:
        logger.warning(
            "[SDAT] Maximale Tiefe %s erreicht, Unterverzeichnisse ausgelassen: %s",
            MAX_RECURSION_DEPTH,
            ", ".join(subdirs),
        )
    elif subdirs and not recursive:
        logger.info(
            "[SDAT] %s Unterverzeichnis(se) übersprungen, --recursive nutzen: %s",
            len(subdirs),
            ", ".join(subdirs),
        )

    return entries


def list_remote_files(
    client, remote_dir: str | None = None, recursive: bool = False
) -> list[RemoteFile]:
    """List the files in the outbox, newest first.

    Prefers MLSD (size and timestamp in one round trip) and falls back to
    NLST + SIZE/MDTM on servers that do not implement it. The NLST fallback
    covers the configured directory only and cannot recurse.
    """
    if remote_dir:
        client.cwd(remote_dir)

    try:
        entries = _walk_mlsd(client, recursive=recursive)
    except (ftplib.error_perm, ftplib.error_proto, AttributeError):
        if recursive:
            logger.warning("[SDAT] MLSD nicht unterstützt, --recursive wird ignoriert")
        else:
            logger.info("[SDAT] MLSD nicht unterstützt, nutze NLST")
        entries = _list_via_nlst(client, remote_dir)

    return sort_newest_first(entries)


def sort_newest_first(entries: Iterable[RemoteFile]) -> list[RemoteFile]:
    """Newest modification time first; entries without a timestamp sort last."""
    return sorted(
        entries,
        key=lambda f: (
            f.modified is not None,
            f.modified or datetime.min.replace(tzinfo=timezone.utc),
            f.name,
        ),
        reverse=True,
    )


def download_file(client, remote: RemoteFile, target: Path) -> int:
    """Download and verify one file atomically. Returns the byte count written."""
    partial = target.with_name(target.name + ".part")
    written = 0
    try:
        with open(partial, "wb") as handle:

            def write_chunk(chunk: bytes) -> None:
                nonlocal written
                written += len(chunk)
                handle.write(chunk)

            client.retrbinary(f"RETR {remote.path}", write_chunk)
        expected = remote.size
        if not expected:
            try:
                expected = int(client.size(remote.path) or 0)
            except (*ftplib.all_errors, AttributeError, TypeError, ValueError):
                expected = 0
        if not expected:
            raise TransferError(f"Remote-Grösse für {remote.path} nicht verifizierbar.")
        if written != expected:
            raise TransferError(
                f"Download unvollständig für {remote.path}: "
                f"{written} statt {expected} Bytes."
            )
        remote.size = expected
        os.replace(partial, target)
        return written
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def fetch_latest(
    config: DatahubConfig | None = None,
    *,
    client=None,
    since: datetime | None = None,
    limit: int | None = None,
    pattern: str | None = None,
    recursive: bool = False,
    force: bool = False,
    dry_run: bool = False,
    delete_remote: bool = False,
) -> dict:
    """Download SDAT files from the Datahub outbox into ``config.local_dir``.

    Args:
        config: connection settings; loaded from the environment when omitted.
        client: an open FTP client; one is opened and closed when omitted.
        since: only take files modified at or after this time; files without a
            modified timestamp are included so an unavailable timestamp cannot
            hide a delivery.
        limit: only take the N newest files.
        pattern: glob matched against the file name (for example ``*.xml``).
        recursive: also descend into subfolders of the remote directory.
        force: re-download files that already exist locally.
        dry_run: list what would be downloaded, write nothing.
        delete_remote: delete each file on the Datahub after a successful
            download. Off by default: the outbox is the only copy until the
            local file is verified.

    Returns:
        A summary dict with the keys ``listed``, ``pending``, ``downloaded``,
        ``skipped``, ``failed``, ``deleted`` and ``bytes``.
    """
    config = config or load_config()
    owns_client = client is None
    if owns_client:
        client = connect(config)

    local_dir = Path(config.local_dir)
    summary: dict = {
        "local_dir": str(local_dir),
        "listed": 0,
        "pending": [],
        "downloaded": [],
        "skipped": [],
        "failed": [],
        "deleted": [],
        "bytes": 0,
    }

    try:
        remote_files = list_remote_files(client, config.remote_dir, recursive=recursive)
        summary["listed"] = len(remote_files)

        if pattern:
            remote_files = [f for f in remote_files if _matches(f.name, pattern)]
        if since is not None:
            remote_files = [
                f for f in remote_files if f.modified is None or f.modified >= since
            ]

        pending = []
        for remote in remote_files:
            if not _is_safe_relpath(remote.path):
                logger.warning("[SDAT] Pfad übersprungen (unsicher): %r", remote.path)
                continue
            if not force and _already_downloaded(
                _target_for(local_dir, remote), remote
            ):
                summary["skipped"].append(remote.path)
                continue
            pending.append(remote)

        if limit is not None and limit >= 0:
            dropped = pending[limit:]
            pending = pending[:limit]
            if dropped:
                logger.info(
                    "[SDAT] %s weitere Dateien wegen --limit ausgelassen", len(dropped)
                )

        summary["pending"] = [f.path for f in pending]
        if dry_run:
            return summary

        for remote in pending:
            target = _target_for(local_dir, remote)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                written = download_file(client, remote, target)
                summary["bytes"] += written
                summary["downloaded"].append(remote.path)
                logger.info("[SDAT] Geladen: %s", remote.path)
            except Exception as exc:
                summary["failed"].append(remote.path)
                logger.error(
                    "[SDAT] Download fehlgeschlagen für %s: %s", remote.path, exc
                )
                continue

            if delete_remote:
                # Nur löschen, wenn der lokale Stand nachweislich vollständig
                # ist. Ein Server kann den Datenkanal früh schliessen, ohne zu
                # melden; dann sieht der Transfer erfolgreich aus und nur die
                # Bytezahl verrät ihn. Die Datahub-Kopie ist die einzige andere
                # Kopie dieser Lieferung, darum ist Behalten der sichere Fehler.
                if not _transfer_is_complete(remote, written):
                    logger.error(
                        "[SDAT] %s bleibt auf dem Datahub: %s statt %s Bytes.",
                        remote.path,
                        written,
                        remote.size,
                    )
                    continue
                try:
                    client.delete(remote.path)
                    summary["deleted"].append(remote.path)
                except Exception as exc:
                    logger.error(
                        "[SDAT] Löschen auf dem Datahub fehlgeschlagen für %s: %s",
                        remote.path,
                        exc,
                    )

        summary["pending"] = []
        return summary
    finally:
        if owns_client:
            try:
                client.quit()
            except Exception:
                client.close()


def _transfer_is_complete(remote: RemoteFile, written: int) -> bool:
    """Ist die lokale Datei nachweislich vollständig?

    Ohne gemeldete Grösse gibt es nichts zu vergleichen. Dann gilt der Transfer
    als unbestätigt: die Datei bleibt auf dem Datahub liegen, statt auf ein
    Versprechen hin gelöscht zu werden.
    """
    if not remote.size:
        return False
    return written == remote.size


def _target_for(local_dir: Path, remote: RemoteFile) -> Path:
    """Local destination, mirroring the remote subfolder layout."""
    return local_dir.joinpath(*remote.path.split("/"))


def _matches(name: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(name.lower(), pattern.lower())


def _already_downloaded(target: Path, remote: RemoteFile) -> bool:
    """A local file counts as done when it exists at the advertised size."""
    if not target.exists():
        return False
    if not remote.size:
        logger.info("[SDAT] Remote-Grösse unbekannt, lade %s erneut", remote.name)
        return False
    if target.stat().st_size != remote.size:
        logger.info("[SDAT] %s hat sich geändert, lade erneut", remote.name)
        return False
    return True
