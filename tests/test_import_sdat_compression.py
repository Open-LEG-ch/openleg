# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for compressing legacy plain SDAT files after database import.

The Datahub ships ``*.xml.gz`` and the importer reads those archives directly.
Legacy plain XML files are the bulk of ``data/`` and stay there forever, so
once a document is safely in the database its XML goes into a gzip and the
plain file is removed.

Compression is allowed after an E66 reached the database and for an explicitly
recognised E31 sibling, whose validated gzip remains readable by the importer.
A dry run, parse failure, or unknown document must leave the file untouched,
because the file is the only local copy of that delivery.
"""

import argparse
import gzip
import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_sdat.py"
FIXTURE = ROOT / "tests" / "fixtures" / "sdat_e66_sample.xml"

E31_DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<rsm:AggregatedMeteredData_13 xmlns:rsm="http://www.strom.ch">
  <rsm:AggregatedMeteredData_HeaderInformation>
    <rsm:InstanceDocument>
      <rsm:DocumentID>AGG-1</rsm:DocumentID>
      <rsm:DocumentType listAgencyID="260">
        <rsm:ebIXCode>E31</rsm:ebIXCode>
      </rsm:DocumentType>
    </rsm:InstanceDocument>
  </rsm:AggregatedMeteredData_HeaderInformation>
</rsm:AggregatedMeteredData_13>"""


@pytest.fixture
def importer():
    """Load scripts/import_sdat.py as a module so helpers are callable."""
    spec = importlib.util.spec_from_file_location("import_sdat_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeDb:
    """Stands in for database.py. Records what the importer asked it to do."""

    def __init__(self, already_imported=False):
        self.already_imported = already_imported
        self.saved = []
        self.recorded = []

    def get_sdat_import(self, document_id):
        return {"document_id": document_id} if self.already_imported else None

    def get_sdat_import_index(self):
        # Empty, so nothing is skipped by name here. Packing is what this file
        # covers; the skip layers have their own contract in
        # tests/test_import_sdat_skip.py.
        ids = frozenset({"TESTDOC-1"}) if self.already_imported else frozenset()
        return {"document_ids": ids, "file_names": frozenset()}

    def save_metering_point_readings(self, rows, source_document_id=None):
        self.saved.append(source_document_id)
        return {"new": len(rows), "corrected": 0, "unchanged": 0, "samples": []}

    def record_sdat_import(self, document):
        self.recorded.append((document["document_id"], document.get("file_name")))
        return True

    def init_db(self):
        return True


def _args(**overrides):
    defaults = {"dry_run": False, "force": False, "quiet": True, "compress": True}
    return argparse.Namespace(**{**defaults, **overrides})


def _staged(tmp_path, name="sample.xml"):
    target = tmp_path / name
    shutil.copy(FIXTURE, target)
    return target


def _broken_document():
    content = FIXTURE.read_text(encoding="utf-8").replace("TESTDOC-1", "BROKEN-1")
    assert len(content) > 2000, "fixture must be long enough to truncate"
    return content[:2000]


# ==== The compression helper ====


def test_compress_replaces_the_xml_with_a_gz(importer, tmp_path):
    path = _staged(tmp_path)

    result = importer.compress_imported_file(path)

    assert result == tmp_path / "sample.xml.gz"
    assert result.exists()
    assert not path.exists(), "the plain XML must be gone, or nothing is saved"


def test_compressed_file_round_trips_to_the_original_bytes(importer, tmp_path):
    original = FIXTURE.read_bytes()
    path = _staged(tmp_path)

    result = importer.compress_imported_file(path)

    with gzip.open(result, "rb") as handle:
        assert handle.read() == original


def test_compression_actually_shrinks_the_file(importer, tmp_path):
    path = _staged(tmp_path)
    before = path.stat().st_size

    result = importer.compress_imported_file(path)

    assert result.stat().st_size < before


def test_compression_leaves_no_partial_file_behind(importer, tmp_path):
    path = _staged(tmp_path)

    importer.compress_imported_file(path)

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")]
    assert leftovers == []


def test_an_already_compressed_file_is_left_alone(importer, tmp_path):
    archive = tmp_path / "sample.xml.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(FIXTURE.read_bytes())
    before = archive.read_bytes()

    assert importer.compress_imported_file(archive) is None
    assert archive.read_bytes() == before, "double compression would corrupt the name"


def test_compression_conflict_preserves_existing_archive_and_plain_source(
    importer, tmp_path
):
    source = _staged(tmp_path)
    archive = tmp_path / "sample.xml.gz"
    archive.write_bytes(b"existing archive")

    with pytest.raises(FileExistsError):
        importer.compress_imported_file(source)

    assert source.read_bytes() == FIXTURE.read_bytes()
    assert archive.read_bytes() == b"existing archive"
    assert not (tmp_path / "sample.xml.gz.part").exists()


def test_compression_race_preserves_competing_archive_and_plain_source(
    importer, tmp_path, monkeypatch
):
    """A target created after the pre-check must never be overwritten."""
    source = _staged(tmp_path)
    archive = tmp_path / "sample.xml.gz"
    real_link = importer.os.link

    def competing_link(partial, target):
        archive.write_bytes(b"competing archive")
        return real_link(partial, target)

    monkeypatch.setattr(importer.os, "link", competing_link)

    with pytest.raises(FileExistsError):
        importer.compress_imported_file(source)

    assert source.read_bytes() == FIXTURE.read_bytes()
    assert archive.read_bytes() == b"competing archive"
    assert not (tmp_path / "sample.xml.gz.part").exists()


def test_the_original_survives_when_compression_fails(importer, tmp_path, monkeypatch):
    path = _staged(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(importer.gzip, "open", _boom)

    with pytest.raises(OSError):
        importer.compress_imported_file(path)

    assert path.exists(), "a failed compression must never lose the delivery"


# ==== When the importer compresses ====


def test_a_stored_document_is_compressed(importer, tmp_path, monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    path = _staged(tmp_path)

    status, _ = importer._import_file(str(path), _args())

    assert status == "imported"
    assert fake.saved, "the rows must reach the database before compression"
    assert not path.exists()
    assert (tmp_path / "sample.xml.gz").exists()


def test_an_already_imported_document_is_compressed_too(
    importer, tmp_path, monkeypatch
):
    # The pipeline unpacks every *.xml.gz in the directory on each run. If a
    # document already in the database were left as plain XML, the next run
    # would undo the saving and the file would never shrink again.
    fake = _FakeDb(already_imported=True)
    monkeypatch.setattr(importer, "db", fake)
    path = _staged(tmp_path)

    status, _ = importer._import_file(str(path), _args())

    assert status == "already", "reported apart from an E31 or foreign skip"
    assert not fake.saved, "an already imported document must not be written again"
    assert (tmp_path / "sample.xml.gz").exists()
    assert not path.exists()


def test_a_dry_run_never_compresses(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = _staged(tmp_path)

    importer._import_file(str(path), _args(dry_run=True))

    assert path.exists(), "a dry run must not touch the directory"
    assert not (tmp_path / "sample.xml.gz").exists()


def test_no_compress_keeps_the_plain_xml(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = _staged(tmp_path)

    importer._import_file(str(path), _args(compress=False))

    assert path.exists()
    assert not (tmp_path / "sample.xml.gz").exists()


def test_an_e31_sibling_is_compressed(importer, tmp_path, monkeypatch):
    # E31 never reaches the database, but it is not pending work either: the
    # importer skips it by design and nothing reads it again. It arrives with
    # every delivery, so leaving it plain would keep the directory large.
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "aggregate.xml"
    path.write_text(E31_DOCUMENT, encoding="utf-8")

    status, _ = importer._import_file(str(path), _args())

    assert status == "skipped"
    assert not path.exists()
    assert (tmp_path / "aggregate.xml.gz").exists()


def test_a_compressed_e31_round_trips(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "aggregate.xml"
    path.write_text(E31_DOCUMENT, encoding="utf-8")

    importer._import_file(str(path), _args())

    with gzip.open(tmp_path / "aggregate.xml.gz", "rt", encoding="utf-8") as handle:
        assert handle.read() == E31_DOCUMENT


def test_an_unrecognised_file_is_not_compressed(importer, tmp_path, monkeypatch):
    # Neither E66 nor E31. It may be a delivery problem an operator has to look
    # at, so it stays readable on disk rather than being packed away.
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "foreign.xml"
    path.write_text("<not-sdat/>", encoding="utf-8")

    status, _ = importer._import_file(str(path), _args())

    assert status == "skipped"
    assert path.exists()
    assert not (tmp_path / "foreign.xml.gz").exists()


def test_a_dry_run_does_not_compress_an_e31(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "aggregate.xml"
    path.write_text(E31_DOCUMENT, encoding="utf-8")

    importer._import_file(str(path), _args(dry_run=True))

    assert path.exists()
    assert not (tmp_path / "aggregate.xml.gz").exists()


def test_no_compress_keeps_a_plain_e31(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "aggregate.xml"
    path.write_text(E31_DOCUMENT, encoding="utf-8")

    importer._import_file(str(path), _args(compress=False))

    assert path.exists()
    assert not (tmp_path / "aggregate.xml.gz").exists()


def test_an_already_packed_e31_stays_quiet(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    archive = tmp_path / "aggregate.xml.gz"
    with gzip.open(archive, "wt", encoding="utf-8") as handle:
        handle.write(E31_DOCUMENT)
    before = archive.read_bytes()

    status, _ = importer._import_file(str(archive), _args())

    assert status == "skipped"
    assert archive.read_bytes() == before


def test_a_broken_document_is_not_compressed(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb())
    path = tmp_path / "broken.xml"
    path.write_text(_broken_document(), encoding="utf-8")

    status, _ = importer._import_file(str(path), _args())

    assert status == "failed"
    assert path.exists(), "a failed parse leaves the only copy on disk"


def test_a_mixed_directory_packs_only_what_is_settled(importer, tmp_path, monkeypatch):
    """One pass over a realistic delivery directory.

    E66 and E31 arrive together on every delivery and both get packed. The
    unrecognised file and the broken E66 stay plain, because both are things an
    operator may still need to read.
    """
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)

    _staged(tmp_path, "20260807_120000_readings.xml")
    (tmp_path / "20260807_120000_aggregate.xml").write_text(
        E31_DOCUMENT, encoding="utf-8"
    )
    (tmp_path / "unknown.xml").write_text("<not-sdat/>", encoding="utf-8")
    (tmp_path / "broken.xml").write_text(_broken_document(), encoding="utf-8")

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 1, "the broken file must still be reported as a failure"
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "20260807_120000_aggregate.xml.gz",
        "20260807_120000_readings.xml.gz",
        "broken.xml",
        "unknown.xml",
    ]


# ==== Reading what we wrote ====


def test_a_gz_file_is_picked_up_from_a_directory(importer, tmp_path):
    # Compression must not hide the file from the tool that compressed it,
    # otherwise --force can never re-read an imported document.
    with gzip.open(tmp_path / "sample.xml.gz", "wb") as handle:
        handle.write(FIXTURE.read_bytes())

    files, missing = importer._candidate_files([str(tmp_path)])

    assert missing == []
    assert [Path(f).name for f in files] == ["sample.xml.gz"]


def test_a_gz_file_imports_like_a_plain_xml(importer, tmp_path, monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    archive = tmp_path / "sample.xml.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(FIXTURE.read_bytes())

    status, result = importer._import_file(str(archive), _args())

    assert status == "imported", "a gz delivery must import like its plain twin"
    assert result["new"] > 0
    assert fake.saved


def test_a_gz_file_keeps_its_plain_name_in_the_ledger(importer, tmp_path, monkeypatch):
    # The ledger and the report identify a delivery by document id, but the file
    # name is what an operator greps for. ".gz" is packaging, not identity.
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    archive = tmp_path / "sample.xml.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(FIXTURE.read_bytes())

    importer._import_file(str(archive), _args())

    assert fake.recorded, "the import must be recorded"
    _, file_name = fake.recorded[0]
    assert file_name == "sample.xml", (
        "the same delivery must appear under one name whether packed or not"
    )


# ==== Persisted totals must match parsed rows ====


class _PartialSaveDb(_FakeDb):
    """save_metering_point_readings reports fewer persisted rows than parsed."""

    def save_metering_point_readings(self, rows, source_document_id=None):
        self.saved.append(source_document_id)
        # Report only one row persisted, even though the document has nine.
        return {"new": 1, "corrected": 0, "unchanged": 0, "samples": []}


def test_a_mismatch_between_persisted_and_parsed_rows_fails(
    importer, tmp_path, monkeypatch
):
    fake = _PartialSaveDb()
    monkeypatch.setattr(importer, "db", fake)
    path = _staged(tmp_path)

    status, _ = importer._import_file(str(path), _args())

    assert status == "failed"
    assert fake.recorded == [], "record_sdat_import must not be called after a mismatch"
    assert path.exists(), "the plain XML must stay readable when rows are missing"
    assert not (tmp_path / "sample.xml.gz").exists()


class _LedgerRejectDb(_FakeDb):
    """record_sdat_import signals that the ledger did not accept the import."""

    def record_sdat_import(self, document):
        self.recorded.append((document["document_id"], document.get("file_name")))
        return False


def test_a_failed_ledger_record_keeps_the_file_plain(importer, tmp_path, monkeypatch):
    fake = _LedgerRejectDb()
    monkeypatch.setattr(importer, "db", fake)
    path = _staged(tmp_path)

    status, _ = importer._import_file(str(path), _args())

    assert status == "failed"
    assert fake.recorded, "record_sdat_import must have been attempted"
    assert path.exists(), "the XML stays plain when the ledger rejects the import"
    assert not (tmp_path / "sample.xml.gz").exists()
