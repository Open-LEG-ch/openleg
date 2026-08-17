# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for skipping SDAT files that are already in the database.

A municipality directory grows with every delivery and never shrinks, so the
cost of a run must stay far below a full parse of every file. The importer uses
the document id from a bounded head read, backed by one bulk ledger query.

Anything whose identity cannot be established falls through to the full work.
Skipping a file the ledger does not know about would silently lose a delivery,
so every uncertain case costs time instead.
"""

import gzip
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_sdat.py"
FIXTURE = ROOT / "tests" / "fixtures" / "sdat_e66_sample.xml"

DOCUMENT_ID = "TESTDOC-1"

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
    spec = importlib.util.spec_from_file_location("import_sdat_skip_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeDb:
    """A ledger that already holds whatever the test says it holds."""

    def __init__(self, file_names=(), document_ids=()):
        self.index = {
            "file_names": frozenset(file_names),
            "document_ids": frozenset(document_ids),
        }
        self.index_calls = 0
        self.point_queries = []
        self.saved = []

    def get_sdat_import_index(self):
        self.index_calls += 1
        return self.index

    def get_sdat_import(self, document_id):
        self.point_queries.append(document_id)
        return (
            {"document_id": document_id}
            if document_id in self.index["document_ids"]
            else None
        )

    def save_metering_point_readings(self, rows, source_document_id=None):
        self.saved.append(source_document_id)
        return {"new": len(rows), "corrected": 0, "unchanged": 0, "samples": []}

    def record_sdat_import(self, document):
        return True

    def init_db(self):
        return True


@pytest.fixture
def counted_reads(importer, monkeypatch):
    """Count how much of each file the importer actually touched."""
    counts = {"head": 0, "full": 0}
    real_head = importer._read_head
    real_text = importer._read_text

    def head(path, *args, **kwargs):
        counts["head"] += 1
        return real_head(path, *args, **kwargs)

    def text(path, *args, **kwargs):
        counts["full"] += 1
        return real_text(path, *args, **kwargs)

    monkeypatch.setattr(importer, "_read_head", head)
    monkeypatch.setattr(importer, "_read_text", text)
    return counts


def _fixture_bytes(document_id=DOCUMENT_ID):
    content = FIXTURE.read_text(encoding="utf-8").replace(DOCUMENT_ID, document_id)
    return content.encode()


def _staged(tmp_path, name="sample.xml", document_id=DOCUMENT_ID):
    target = tmp_path / name
    target.write_bytes(_fixture_bytes(document_id))
    return target


def _packed(tmp_path, name="sample.xml.gz", document_id=DOCUMENT_ID):
    target = tmp_path / name
    with gzip.open(target, "wb") as handle:
        handle.write(_fixture_bytes(document_id))
    return target


# ==== Identity comes from the document header, never the file name ====


def test_a_known_file_name_does_not_skip_a_different_document(
    importer, tmp_path, monkeypatch, counted_reads
):
    fake = _FakeDb(file_names=["sample.xml"])
    monkeypatch.setattr(importer, "db", fake)
    _packed(tmp_path)

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 0
    assert fake.saved == [DOCUMENT_ID]
    assert counted_reads == {"head": 1, "full": 1}


def test_the_ledger_is_read_once_per_run(importer, tmp_path, monkeypatch):
    # One bulk query, not one per file. That is the whole point.
    fake = _FakeDb(file_names=["a.xml", "b.xml", "c.xml"])
    monkeypatch.setattr(importer, "db", fake)
    for name in ("a.xml.gz", "b.xml.gz", "c.xml.gz"):
        _packed(tmp_path, name)

    importer.main([str(tmp_path)])

    assert fake.index_calls == 1
    assert fake.point_queries == [], "no per-file ledger query may remain"


def test_a_known_name_still_plain_is_packed_once(importer, tmp_path, monkeypatch):
    # Directories from before packing existed are full of plain imported XML.
    # Skipping must not mean they stay large forever.
    monkeypatch.setattr(importer, "db", _FakeDb(file_names=["sample.xml"]))
    path = _staged(tmp_path)

    importer.main([str(tmp_path)])

    assert not path.exists()
    assert (tmp_path / "sample.xml.gz").exists()


def test_a_known_name_already_packed_is_left_untouched(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "db", _FakeDb(file_names=["sample.xml"]))
    archive = _packed(tmp_path)
    before = archive.read_bytes()

    importer.main([str(tmp_path)])

    assert archive.read_bytes() == before
    assert list(tmp_path.iterdir()) == [archive]


def test_the_summary_reports_what_was_skipped(importer, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        importer,
        "db",
        _FakeDb(
            file_names=["a.xml", "b.xml"],
            document_ids=[DOCUMENT_ID],
        ),
    )
    _packed(tmp_path, "a.xml.gz")
    _packed(tmp_path, "b.xml.gz")

    importer.main([str(tmp_path)])

    out = capsys.readouterr().out
    assert "2 bereits importiert" in out, (
        "a run that skips work has to say so, or it looks like it did nothing"
    )


# ==== Known document ids skip the full parse ====


def test_a_renamed_file_is_caught_by_its_document_id(
    importer, tmp_path, monkeypatch, counted_reads
):
    # The id inside the header matches, so it must not be parsed or imported again.
    fake = _FakeDb(file_names=["some-old-name.xml"], document_ids=[DOCUMENT_ID])
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path, "renamed.xml")

    importer.main([str(tmp_path)])

    assert fake.saved == [], "a known document must not be stored twice"
    assert counted_reads["head"] == 1
    assert counted_reads["full"] == 0, "a known id must not trigger a full read"


def test_an_e31_is_classified_from_the_head_alone(
    importer, tmp_path, monkeypatch, counted_reads
):
    monkeypatch.setattr(importer, "db", _FakeDb())
    (tmp_path / "aggregate.xml").write_text(E31_DOCUMENT, encoding="utf-8")

    importer.main([str(tmp_path)])

    assert counted_reads["full"] == 0, "an E31 never needs its whole body"
    assert (tmp_path / "aggregate.xml.gz").exists()


def test_a_new_document_is_still_fully_imported(
    importer, tmp_path, monkeypatch, counted_reads
):
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path)

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 0
    assert fake.saved == [DOCUMENT_ID]
    assert counted_reads["full"] == 1, "a new delivery has to be read in full"


# ==== Falling through when identity is unknown ====


def test_a_document_without_an_extractable_id_is_not_skipped(
    importer, tmp_path, monkeypatch, counted_reads
):
    # extract_document_id returning None must cost time, never correctness.
    fake = _FakeDb(document_ids=[DOCUMENT_ID])
    monkeypatch.setattr(importer, "db", fake)
    monkeypatch.setattr(importer.sdat_e66, "extract_document_id", lambda head: None)
    _staged(tmp_path)

    importer.main([str(tmp_path)])

    assert counted_reads["full"] == 1, "unknown identity means do the work"


def test_an_empty_ledger_processes_everything(importer, tmp_path, monkeypatch):
    # A failed ledger read returns empty sets, which must mean "import", not
    # "skip everything".
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path)

    importer.main([str(tmp_path)])

    assert fake.saved == [DOCUMENT_ID]


def test_duplicate_document_ids_in_one_run_are_written_once(
    importer, tmp_path, monkeypatch
):
    fake = _FakeDb()
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path, "first.xml")
    _staged(tmp_path, "second.xml")

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 0
    assert fake.saved == [DOCUMENT_ID]


def test_failed_bulk_ledger_read_falls_through_to_full_processing(
    importer, tmp_path, monkeypatch
):
    fake = _FakeDb()
    fake.get_sdat_import_index = lambda: (_ for _ in ()).throw(
        RuntimeError("database read failed")
    )
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path)

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 0
    assert fake.saved == [DOCUMENT_ID]


def test_force_bypasses_both_layers(importer, tmp_path, monkeypatch, counted_reads):
    fake = _FakeDb(file_names=["sample.xml"], document_ids=[DOCUMENT_ID])
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path)

    importer.main([str(tmp_path), "--force"])

    assert fake.saved == [DOCUMENT_ID], "--force has to re-read a settled document"
    assert counted_reads["full"] == 1


def test_a_dry_run_needs_no_ledger(importer, tmp_path, monkeypatch):
    # --dry-run works with no database at all, so it must not ask for the index.
    fake = _FakeDb(file_names=["sample.xml"])
    monkeypatch.setattr(importer, "db", fake)
    _staged(tmp_path)

    importer.main([str(tmp_path), "--dry-run"])

    assert fake.index_calls == 0


# ==== One pass over a real directory ====


def test_a_mixed_directory_only_works_on_what_is_new(
    importer, tmp_path, monkeypatch, counted_reads
):
    """The shape of a daily run once the archive has history."""
    fake = _FakeDb(
        file_names=["settled_plain.xml", "settled_packed.xml"],
        document_ids=["OLD-1", "OLD-2"],
    )
    monkeypatch.setattr(importer, "db", fake)

    _staged(tmp_path, "new_delivery.xml")
    _staged(tmp_path, "settled_plain.xml", "OLD-1")
    _packed(tmp_path, "settled_packed.xml.gz", "OLD-2")
    (tmp_path / "aggregate.xml").write_text(E31_DOCUMENT, encoding="utf-8")
    (tmp_path / "unknown.xml").write_text("<not-sdat/>", encoding="utf-8")

    exit_code = importer.main([str(tmp_path)])

    assert exit_code == 0
    assert fake.saved == [DOCUMENT_ID], "only the new delivery reaches the database"
    assert counted_reads["full"] == 1, "one full read, for the new delivery only"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "aggregate.xml.gz",
        "new_delivery.xml.gz",
        "settled_packed.xml.gz",
        "settled_plain.xml.gz",
        "unknown.xml",
    ]
