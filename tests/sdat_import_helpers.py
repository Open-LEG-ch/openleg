# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-memory SDAT storage for tests of the shared import.

The import reaches storage through the ``database`` facade. ``use_storage``
replaces those functions for one test, so the import module, the command and
scheduled ingestion all run their real code against the same ledger.
"""

from pathlib import Path

import database

FIXTURE = Path(__file__).with_name("fixtures") / "sdat_e66_sample.xml"
DOCUMENT_ID = "TESTDOC-1"

STORAGE_FUNCTIONS = (
    "init_db",
    "get_sdat_import",
    "get_sdat_import_index",
    "save_metering_point_readings",
    "record_sdat_import",
    "record_sdat_veracity_flags",
)

# Point 1's first community interval. 0.041 still balances within the parser's
# tolerance, so the changed file imports as one corrected reading.
_COMMUNITY_FIRST_INTERVAL = """    <rsm:Observation>
      <rsm:Position><rsm:Sequence>1</rsm:Sequence></rsm:Position>
      <rsm:Volume>0.040</rsm:Volume>
    </rsm:Observation>"""

_VALUES = ("total_kwh", "grid_kwh", "community_kwh")


class MemoryLedger:
    """Readings and the import ledger, classified the way store/metering does."""

    def __init__(self):
        self.readings = {}
        self.documents = {}
        self.saved = []
        self.point_queries = []
        self.init_calls = 0

    def init_db(self):
        self.init_calls += 1
        return True

    def get_sdat_import(self, document_id):
        self.point_queries.append(document_id)
        return self.documents.get(document_id)

    def get_sdat_import_index(self):
        return {
            "document_ids": frozenset(self.documents),
            "file_names": frozenset(
                entry["file_name"] for entry in self.documents.values()
            ),
        }

    def save_metering_point_readings(self, rows, source_document_id=None):
        self.saved.append(source_document_id)
        result = {"new": 0, "corrected": 0, "unchanged": 0, "samples": []}
        for row in rows:
            key = (row["metering_point_id"], row["direction"], row["measured_at"])
            values = tuple(row[name] for name in _VALUES)
            previous = self.readings.get(key)
            self.readings[key] = values
            if previous is None:
                result["new"] += 1
            elif previous != values:
                result["corrected"] += 1
                result["samples"].append(key)
            else:
                result["unchanged"] += 1
        return result

    def record_sdat_import(self, document):
        self.documents[document["document_id"]] = {
            "document_id": document["document_id"],
            "file_name": document.get("file_name"),
        }
        return True

    def record_sdat_veracity_flags(self, document_id, flags):
        return True


def use_storage(monkeypatch, storage):
    """Route the database functions the import calls to ``storage``."""
    for name in STORAGE_FUNCTIONS:
        monkeypatch.setattr(database, name, getattr(storage, name))
    return storage


def delivery(directory, name="sample.xml", *, document_id=DOCUMENT_ID, corrected=False):
    """Write the fixture E66 as ``name``; ``corrected`` changes one reading."""
    content = FIXTURE.read_text(encoding="utf-8")
    assert _COMMUNITY_FIRST_INTERVAL in content, "fixture shape changed"
    if corrected:
        content = content.replace(
            _COMMUNITY_FIRST_INTERVAL,
            _COMMUNITY_FIRST_INTERVAL.replace("0.040", "0.041"),
            1,
        )
    content = content.replace(DOCUMENT_ID, document_id)
    target = Path(directory) / name
    target.write_text(content, encoding="utf-8")
    return target


def broken_delivery(directory, name="broken.xml"):
    """Write a truncated E66 that the parser rejects."""
    content = FIXTURE.read_text(encoding="utf-8").replace(DOCUMENT_ID, "BROKEN-1")
    target = Path(directory) / name
    target.write_text(content[:2000], encoding="utf-8")
    return target


FIXTURE_READINGS = 9
