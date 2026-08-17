# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the browser-download collector.

The Datahub notification archive hands out ``*.xml.gz`` and ``import_sdat.py``
reads these archives directly. The collector preserves the original bytes,
skips what is already there, and never leaves a corrupt or partial archive.
"""

import gzip
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "collect_sdat_downloads.py"

SDAT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:ValidatedMeteredData_16 xmlns:rsm="http://www.strom.ch">
  <rsm:MeteringData><rsm:DocumentID>E66-1</rsm:DocumentID></rsm:MeteringData>
</rsm:ValidatedMeteredData_16>
"""

# Synthetic Datahub filename: same shape as a real delivery, but the EIC codes
# are all zeros. Real VNB and LEG identifiers must never reach a tracked file.
NAME = (
    "20260808_063024_12X-0000000000-F_E66_12X-00000000AA-P_LGZ_EGZ__LEG_00000001.xml.gz"
)


@pytest.fixture(scope="module")
def collect():
    spec = importlib.util.spec_from_file_location("collect_sdat_downloads", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "downloads"
    path.mkdir()
    return path


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "schwarzenburg"


def write_gz(directory: Path, name: str, payload: bytes = SDAT_XML) -> Path:
    target = directory / name
    target.write_bytes(gzip.compress(payload))
    return target


class TestCollect:
    def test_preserves_archives_in_the_data_directory(self, collect, source, dest):
        archive = write_gz(source, NAME)
        original = archive.read_bytes()
        result = collect.collect(source, dest)

        assert result["written"] == [NAME]
        assert (dest / NAME).read_bytes() == original
        assert gzip.decompress((dest / NAME).read_bytes()) == SDAT_XML

    def test_skips_files_already_collected(self, collect, source, dest):
        write_gz(source, NAME)
        collect.collect(source, dest)

        again = collect.collect(source, dest)
        assert again["written"] == []
        assert again["skipped"] == [NAME]

    def test_force_overwrites_existing_output(self, collect, source, dest):
        write_gz(source, NAME)
        collect.collect(source, dest)

        again = collect.collect(source, dest, force=True)
        assert again["written"] == [NAME]

    def test_ignores_unrelated_files(self, collect, source, dest):
        write_gz(source, NAME)
        (source / "invoice.pdf").write_bytes(b"%PDF-1.4")
        (source / "notes.txt").write_text("hello")

        result = collect.collect(source, dest)
        assert result["written"] == [NAME]

    def test_a_corrupt_archive_fails_without_leaving_a_partial_file(
        self, collect, source, dest
    ):
        (source / NAME).write_bytes(b"this is not gzip")

        result = collect.collect(source, dest)
        assert result["written"] == []
        assert result["failed"] == [NAME]
        assert not (dest / NAME).exists()
        assert list(dest.glob("*")) == []
        assert list(dest.glob("*.part")) == []

    def test_one_bad_archive_does_not_stop_the_batch(self, collect, source, dest):
        write_gz(source, NAME)
        bad = "20260807_063039_12X-0000000000-F_E66_broken.xml.gz"
        (source / bad).write_bytes(b"not gzip")

        result = collect.collect(source, dest)
        assert result["written"] == [NAME]
        assert result["failed"] == [bad]

    def test_handles_the_duplicate_names_chrome_creates(self, collect, source, dest):
        # A second download of the same file becomes "....xml (1).gz".
        duplicate = NAME[: -len(".gz")] + " (1).gz"
        write_gz(source, duplicate)

        result = collect.collect(source, dest)
        assert result["written"] == [NAME]
        assert gzip.decompress((dest / NAME).read_bytes()) == SDAT_XML

    def test_a_duplicate_does_not_overwrite_the_original(self, collect, source, dest):
        write_gz(source, NAME)
        collect.collect(source, dest)
        write_gz(source, NAME[: -len(".gz")] + " (1).gz")

        again = collect.collect(source, dest)
        assert again["written"] == []
        assert again["skipped"] == [NAME]

    def test_a_corrupt_duplicate_does_not_mask_the_valid_original(
        self, collect, source, dest
    ):
        duplicate = NAME[: -len(".gz")] + " (1).gz"
        (source / duplicate).write_bytes(b"truncated")
        write_gz(source, NAME)

        result = collect.collect(source, dest)

        assert result["written"] == [NAME]
        assert result["failed"] == []
        assert gzip.decompress((dest / NAME).read_bytes()) == SDAT_XML

    def test_a_valid_duplicate_recovers_a_corrupt_original(self, collect, source, dest):
        duplicate = NAME[: -len(".gz")] + " (1).gz"
        (source / NAME).write_bytes(b"truncated")
        write_gz(source, duplicate)

        result = collect.collect(source, dest)

        assert result["written"] == [NAME]
        assert result["failed"] == []
        assert gzip.decompress((dest / NAME).read_bytes()) == SDAT_XML

    def test_dry_run_writes_nothing(self, collect, source, dest):
        write_gz(source, NAME)
        result = collect.collect(source, dest, dry_run=True)

        assert result["pending"] == [NAME]
        assert result["written"] == []
        assert not dest.exists()

    def test_move_keeps_archive_when_source_and_destination_are_the_same(
        self, collect, source
    ):
        archive = write_gz(source, NAME)

        result = collect.collect(source, source, force=True, move=True)

        assert result["written"] == [NAME]
        assert archive.exists()
        assert gzip.decompress(archive.read_bytes()) == SDAT_XML

    def test_missing_source_directory_is_reported(self, collect, tmp_path, dest):
        with pytest.raises(collect.CollectError):
            collect.collect(tmp_path / "nope", dest)


class TestScriptContract:
    def test_script_exists_and_is_executable(self):
        import os

        assert SCRIPT.exists()
        assert os.access(SCRIPT, os.X_OK)

    def test_default_destination_is_generic_sdat_directory(self, collect):
        assert collect.DEFAULT_DEST == "data/sdat"

    def test_browser_helper_is_documented_next_to_it(self):
        helper = ROOT / "scripts" / "datahub_download_notifications.js"
        assert helper.exists()
        text = helper.read_text(encoding="utf-8")
        # The snippet must not carry a hardcoded session token or password.
        assert "Authorization" not in text
        assert "password" not in text.lower()

    def test_browser_helper_uses_swiss_high_german(self):
        """Source contract: the JS helper's German text must use real umlauts."""
        helper = ROOT / "scripts" / "datahub_download_notifications.js"
        text = helper.read_text(encoding="utf-8")
        forbidden = (
            "Oberflaeche",
            "oeffnen",
            "vollstaendig",
            "einfuegen",
            "fuer",
            "Eintraege",
            "faellt",
            "Zurueckblaettern",
            "zurueck",
            "laeuft",
            "ueber",
            "ueberlebt",
            "Zurueck",
            "Naechster",
            "Seitengroesse",
        )
        found = [word for word in forbidden if word in text]
        assert not found, f"Found forbidden umlaut transliterations: {found}"

    def test_browser_helper_waits_for_the_response_and_restores_xhr_hooks(self):
        helper = ROOT / "scripts" / "datahub_download_notifications.js"
        text = helper.read_text(encoding="utf-8")

        assert "const waitForDownloadResponse" in text
        register = text.index("const response = waitForDownloadResponse();")
        click = text.index("download.click();", register)
        wait = text.index("const status = await response;", click)
        assert register < click < wait
        assert "status === null" in text, "a bounded wait must fail on timeout"
        assert "if (!serverError) consecutiveServerErrors = 0;" in text
        assert "await sleep(AFTER_DOWNLOAD_MS)" not in text
        assert text.count("restore();") == 1
        assert "finally {\n    restore();\n  }" in text

    def test_browser_helper_fails_closed_when_full_list_is_not_guaranteed(self):
        helper = ROOT / "scripts" / "datahub_download_notifications.js"
        text = helper.read_text(encoding="utf-8")

        assert "if (!(await setPageSize()))" in text
        assert "if (rows.length >= PAGE_SIZE)" in text
        assert "Vollständige Liste nicht garantiert" in text

    def test_browser_helper_counts_timeouts_and_aborts_as_server_failures(self):
        helper = ROOT / "scripts" / "datahub_download_notifications.js"
        text = helper.read_text(encoding="utf-8")

        timeout = text[
            text.index("if (status === null)") : text.index("if (status === 0)")
        ]
        aborted = text[
            text.index("if (status === 0)") : text.index("if (status >= 400)")
        ]
        for branch in (timeout, aborted):
            assert "serverError = true" in branch
            assert "consecutiveServerErrors++" in branch
