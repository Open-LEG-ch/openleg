# SPDX-License-Identifier: AGPL-3.0-or-later
"""SDAT E66 import shared by the import command and scheduled ingestion.

``import_paths`` owns discovery, document ordering, ledger replay, parsing,
persistence checks, compression and totals. It returns an ``ImportReport`` of
structured outcomes. ``scripts/import_sdat.py`` turns them into German text and
an exit code; ``sdat_ingestion`` turns them into safe counts. This module never
prints and never touches process-wide streams.

Storage goes through the ``database`` facade and its connection pool. A caller
that already runs a live pool passes nothing; the command passes
``initialize`` to open one.

Packing: once the rows are in the database, the import stores the XML as
``.gz`` and removes the original. ``.xml.gz`` is read directly, so ``force``
still works after packing.
"""

from __future__ import annotations

import gzip
import os
import secrets
import shutil
import zlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import sdat_e66

GZ_SUFFIX = ".gz"

# Enough for the document type and DocumentID: in the sample the ID starts at
# character 1369. Generous so a long header still fits; cheap next to a parse.
HEAD_LIMIT = 16384

# File outcomes.
IMPORTED = "imported"
ALREADY = "already"
SKIPPED = "skipped"
FAILED = "failed"
STATUSES = (IMPORTED, ALREADY, SKIPPED, FAILED)

# Run diagnostics.
MISSING_PATH = "missing_path"
STORAGE_UNAVAILABLE = "storage_unavailable"
LEDGER_UNREADABLE = "ledger_unreadable"

# File diagnostics. The first four fail the file; the last two are warnings.
UNREADABLE = "unreadable"
INVALID_DOCUMENT = "invalid_document"
INCOMPLETE_PERSISTENCE = "incomplete_persistence"
LEDGER_NOT_RECORDED = "ledger_not_recorded"
VERACITY_FLAGS_NOT_RECORDED = "veracity_flags_not_recorded"
COMPRESSION_FAILED = "compression_failed"

# zlib.error is what a broken deflate stream raises; it is not an OSError.
_READ_ERRORS = (OSError, EOFError, gzip.BadGzipFile, zlib.error, UnicodeDecodeError)
_PACK_ERRORS = (OSError, EOFError, gzip.BadGzipFile, zlib.error)


@dataclass(frozen=True)
class Diagnostic:
    """One machine-readable finding. ``detail`` is for the operator's console."""

    code: str
    detail: str = ""


@dataclass(frozen=True)
class DocumentSummary:
    """What a parsed E66 contained, without its rows."""

    doc_type: str
    document_id: str
    period_start: Any
    period_end: Any
    point_count: int
    block_count: int
    row_count: int
    warnings: tuple[str, ...]
    veracity_flag_count: int


@dataclass(frozen=True)
class ReadingCounts:
    """How storage classified written readings.

    ``samples`` holds unmasked ``(metering_point_id, direction, measured_at)``
    keys of corrected readings; mask them before display.
    """

    new: int = 0
    corrected: int = 0
    unchanged: int = 0
    samples: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True)
class FileOutcome:
    """The settled result of one file. ``name`` drops the ``.gz`` suffix."""

    path: str
    name: str
    status: str
    document: DocumentSummary | None = None
    readings: ReadingCounts | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ImportReport:
    """Every file outcome and run diagnostic of one ``import_paths`` call."""

    files: tuple[FileOutcome, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def count(self, status: str) -> int:
        return sum(outcome.status == status for outcome in self.files)

    @property
    def readings(self) -> ReadingCounts:
        written = [outcome.readings for outcome in self.files if outcome.readings]
        return ReadingCounts(
            new=sum(item.new for item in written),
            corrected=sum(item.corrected for item in written),
            unchanged=sum(item.unchanged for item in written),
        )

    @property
    def storage_unavailable(self) -> bool:
        return any(item.code == STORAGE_UNAVAILABLE for item in self.diagnostics)

    @property
    def ledger_unreadable(self) -> bool:
        return any(item.code == LEDGER_UNREADABLE for item in self.diagnostics)

    @property
    def succeeded(self) -> bool:
        """No missing path, no unavailable storage or ledger, no failed file."""
        return self.count(FAILED) == 0 and not any(
            item.code in (MISSING_PATH, STORAGE_UNAVAILABLE, LEDGER_UNREADABLE)
            for item in self.diagnostics
        )


Reporter = Callable[[FileOutcome | Diagnostic], None]


@dataclass(frozen=True)
class _Options:
    dry_run: bool
    force: bool
    compress: bool


def import_paths(
    paths: Iterable[str],
    *,
    dry_run: bool = False,
    force: bool = False,
    compress: bool = True,
    initialize: Callable[[], bool] | None = None,
    reporter: Reporter | None = None,
) -> ImportReport:
    """Import every SDAT file below ``paths`` and return what happened.

    Files run oldest first so the last write wins. Each file persists on its
    own, so one bad file never undoes the rest of the batch. A dry run and a
    run without files never touch storage. ``initialize`` runs once, only when
    a live import with files needs storage; a false result stops the run, and
    so does an unreadable ledger unless ``force`` is set.
    ``reporter`` receives each run diagnostic and file outcome as it settles;
    the returned report never depends on it.
    """
    options = _Options(dry_run=dry_run, force=force, compress=compress)
    files, missing = _candidate_files(paths)
    run_diagnostics: list[Diagnostic] = []

    def note(diagnostic: Diagnostic) -> None:
        run_diagnostics.append(diagnostic)
        if reporter is not None:
            reporter(diagnostic)

    for path in missing:
        note(Diagnostic(MISSING_PATH, path))

    storage = None
    known: set[str] = set()
    if files and not dry_run:
        if initialize is not None and not initialize():
            note(Diagnostic(STORAGE_UNAVAILABLE))
            return ImportReport(diagnostics=tuple(run_diagnostics))
        storage = _storage()
        # One ledger query per run instead of one per file. ``force`` ignores
        # the ledger, so it never reads it.
        if not force:
            try:
                known = set(storage.get_sdat_import_index()["document_ids"])
            except Exception:
                # An unreadable ledger is not an empty one. Replaying a settled
                # delivery could overwrite its later correction (#941).
                note(Diagnostic(LEDGER_UNREADABLE))
                return ImportReport(diagnostics=tuple(run_diagnostics))

    outcomes = []
    for path in sorted(files, key=_sort_key):
        outcome = _import_file(path, options, storage, known)
        outcomes.append(outcome)
        if reporter is not None:
            reporter(outcome)
    return ImportReport(files=tuple(outcomes), diagnostics=tuple(run_diagnostics))


def compress_imported_file(path: str | Path) -> Path | None:
    """Store the imported XML as ``.gz`` and remove the original.

    Returns the archive path, or ``None`` when the file is already packed.
    Write, verify, then delete: the file is the only local copy of the
    delivery, so it disappears only against an archive that demonstrably
    unpacks again.
    """
    path = Path(path)
    if path.name.lower().endswith(GZ_SUFFIX):
        return None

    target = path.with_name(path.name + GZ_SUFFIX)
    if target.exists():
        raise FileExistsError(f"Archiv existiert bereits: {target.name}")
    original_size = path.stat().st_size
    with _partial_archive(target) as (partial, raw):
        with (
            open(path, "rb") as source,
            gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw) as sink,
        ):
            shutil.copyfileobj(source, sink)
        raw.close()
        written = 0
        with gzip.open(partial, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                written += len(chunk)
        if written != original_size:
            raise OSError(
                f"Archiv unvollständig: {target.name} "
                f"({written} statt {original_size} Bytes)"
            )
        # A hard link instead of replace: if the target appeared since the
        # check above, this fails atomically and neither delivery is lost.
        try:
            os.link(partial, target)
        except FileExistsError as exc:
            raise FileExistsError(f"Archiv existiert bereits: {target.name}") from exc

    path.unlink(missing_ok=True)
    return target


@contextmanager
def _partial_archive(target: Path) -> Iterator[tuple[Path, BinaryIO]]:
    """This call's own temporary archive next to ``target``, removed on exit.

    Each call creates a random name exclusively, so concurrent imports of one
    delivery never write, link or delete each other's unfinished archive; a
    name collision fails the pack and leaves the other file alone. The
    26-character name stays within any file-name limit, whatever the delivery
    is called.
    """
    partial = target.with_name(f"sdat-{secrets.token_hex(8)}.part")
    name = os.fspath(partial)
    try:
        # Only C code runs from here to the exclusive create (barring Python
        # audit hooks), so an interrupt caught below landed after it.
        raw = open(name, "xb")  # noqa: SIM115 - the with below owns it
    except Exception:
        raise  # The create failed; whatever holds the name is not ours.
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    with raw:
        try:
            yield partial, raw
        finally:
            partial.unlink(missing_ok=True)


def _storage() -> Any:
    """The database facade, imported only when a live import needs it."""
    import database

    return database


def _is_sdat_file(name: str) -> bool:
    return name.lower().endswith((".xml", ".xml" + GZ_SUFFIX))


def _plain_name(name: str) -> str:
    """``foo.xml.gz`` is ``foo.xml`` in outcomes and the ledger.

    Compression is packaging, not identity: one delivery keeps one name
    whether packed or not.
    """
    return name[: -len(GZ_SUFFIX)] if name.lower().endswith(GZ_SUFFIX) else name


def _candidate_files(paths: Iterable[str]) -> tuple[list[str], list[str]]:
    """Collect files; search directories for *.xml and *.xml.gz."""
    files, missing = [], []
    for raw in paths:
        if os.path.isdir(raw):
            for name in sorted(os.listdir(raw)):
                if _is_sdat_file(name):
                    files.append(os.path.join(raw, name))
        elif os.path.isfile(raw):
            files.append(raw)
        else:
            missing.append(raw)
    return files, missing


def _sort_key(path: str) -> str:
    """Sort by the chronological base file name.

    Last write wins only when older documents run first. File names start with
    YYYYMMDD_HHMMSS and therefore sort chronologically.
    """
    return os.path.basename(path)


def _read_text(path: str) -> str:
    """Read the whole file, unpacking ``.gz`` transparently."""
    if path.lower().endswith(GZ_SUFFIX):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _read_head(path: str, limit: int = HEAD_LIMIT) -> str:
    """Read only the start, enough for the type and the ID.

    For ``.gz`` this unpacks the first blocks, not the whole file.
    """
    if path.lower().endswith(GZ_SUFFIX):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read(limit)
    with open(path, encoding="utf-8") as handle:
        return handle.read(limit)


def _compress(path: str, options: _Options) -> tuple[Diagnostic, ...]:
    """Pack after import. A failure costs disk space, not data."""
    if options.dry_run or not options.compress:
        return ()
    try:
        compress_imported_file(path)
    except _PACK_ERRORS as exc:
        return (Diagnostic(COMPRESSION_FAILED, str(exc)),)
    return ()


def _already_imported(
    document_id: str | None, options: _Options, known: set[str]
) -> bool:
    """Is this document in the ledger? ``force`` ignores the ledger."""
    if options.force or not document_id:
        return False
    return document_id in known


def _summary(document: dict[str, Any]) -> DocumentSummary:
    return DocumentSummary(
        doc_type=document["doc_type"],
        document_id=document["document_id"],
        period_start=document.get("period_start"),
        period_end=document.get("period_end"),
        point_count=len(document.get("point_ids", [])),
        block_count=document.get("block_count", 0),
        row_count=len(document.get("rows", [])),
        warnings=tuple(document.get("warnings", [])),
        veracity_flag_count=len(document.get("veracity_flags", [])),
    )


def _import_file(
    path: str, options: _Options, storage: Any, known: set[str]
) -> FileOutcome:
    """Settle one file.

    Cheap decisions come first: the head, then the ID, and only for a new
    delivery the whole file. A full parse costs about 2500 head scans and grows
    with the file.
    """
    name = _plain_name(os.path.basename(path))

    def outcome(status: str, *diagnostics: Diagnostic, **fields: Any) -> FileOutcome:
        return FileOutcome(path, name, status, diagnostics=diagnostics, **fields)

    try:
        head = _read_head(path)
    except _READ_ERRORS as exc:
        return outcome(FAILED, Diagnostic(UNREADABLE, str(exc)))

    if not sdat_e66.is_e66_document(head):
        # E31 siblings arrive with every download and count as skipped. A
        # recognised E31 is packed: nobody reads it again. Any other file stays
        # readable, because it may be a delivery problem someone must inspect.
        if sdat_e66.is_e31_document(head):
            return outcome(SKIPPED, *_compress(path, options))
        return outcome(SKIPPED)

    # The ID from the head is the identity. When it is unreadable, the file is
    # processed in full rather than skipped.
    if not options.dry_run and _already_imported(
        sdat_e66.extract_document_id(head), options, known
    ):
        return outcome(ALREADY, *_compress(path, options))

    try:
        content = _read_text(path)
    except _READ_ERRORS as exc:
        return outcome(FAILED, Diagnostic(UNREADABLE, str(exc)))

    document, errors = sdat_e66.parse_e66_xml(content)
    if errors:
        return outcome(
            FAILED, *(Diagnostic(INVALID_DOCUMENT, str(error)) for error in errors)
        )

    document["file_name"] = name
    summary = _summary(document)
    if options.dry_run:
        return outcome(IMPORTED, document=summary)

    # Fallback: the head gave no ID, but the parsed ID is already in the ledger.
    if _already_imported(document["document_id"], options, known):
        return outcome(ALREADY, *_compress(path, options), document=summary)

    result = storage.save_metering_point_readings(
        document["rows"], source_document_id=document["document_id"]
    )
    # Storage must account for every parsed row. One missing row means the
    # delivery is not stored: no ledger entry, no success, no packing.
    persisted = result["new"] + result["corrected"] + result["unchanged"]
    if persisted != len(document["rows"]):
        return outcome(FAILED, Diagnostic(INCOMPLETE_PERSISTENCE), document=summary)

    if not storage.record_sdat_import(
        {
            **document,
            "row_count": len(document["rows"]),
            "new_count": result["new"],
            "corrected_count": result["corrected"],
        }
    ):
        return outcome(FAILED, Diagnostic(LEDGER_NOT_RECORDED), document=summary)

    # Veracity flags (#517) block nothing. If recording them fails, the import
    # stays valid; only their visibility is missing.
    diagnostics: list[Diagnostic] = []
    if not storage.record_sdat_veracity_flags(
        document["document_id"], document.get("veracity_flags", [])
    ):
        diagnostics.append(Diagnostic(VERACITY_FLAGS_NOT_RECORDED))

    known.add(document["document_id"])
    readings = ReadingCounts(
        new=result["new"],
        corrected=result["corrected"],
        unchanged=result["unchanged"],
        samples=tuple(tuple(sample) for sample in result.get("samples", ())),
    )
    diagnostics.extend(_compress(path, options))
    return outcome(IMPORTED, *diagnostics, document=summary, readings=readings)
