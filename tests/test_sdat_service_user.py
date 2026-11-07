# SPDX-License-Identifier: AGPL-3.0-or-later
"""The container's service user can store a scheduled SDAT delivery.

The image runs gunicorn unprivileged and leaves the application source owned
by root. The documented default `SWISSELDEX_SDAT_DIR=data/sdat` resolves below
`WORKDIR`, so the Dockerfile has to hand that directory to the service user.
These tests read the ownership the Dockerfile grants, then replay a synthetic
delivery through the real resolver and downloader in a tree laid out the same
way: only the directories the Dockerfile gives the service user are writable.
"""

import os
import shlex
from pathlib import Path, PurePosixPath

import pytest

import sdat_datahub
import sdat_ingestion
from tests.test_sdat_datahub import SDAT_XML, FakeFTP

PROJECT_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.deploy


def _dockerfile():
    """WORKDIR, the final USER and every path chowned to that user."""
    logical = (PROJECT_ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    workdir = user = None
    chowned = []
    for line in logical.splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if words[0] == "WORKDIR":
            workdir = PurePosixPath(words[1])
        elif words[0] == "USER":
            user = words[1].split(":")[0]
        elif words[0] == "RUN":
            for index, word in enumerate(words):
                if word != "chown":
                    continue
                args = [w for w in words[index + 1 :] if not w.startswith("-")]
                owner, paths = args[0], []
                for arg in args[1:]:
                    if arg in {"&&", "||", ";"}:
                        break
                    paths.append(PurePosixPath(arg))
                chowned.append((owner.split(":")[0], paths))
    owned = [path for owner, paths in chowned if owner == user for path in paths]
    return workdir, user, owned


def _inside(path, owned):
    return any(path == root or root in path.parents for root in owned)


@pytest.mark.parametrize("module", [sdat_ingestion, sdat_datahub])
def test_the_default_sdat_root_belongs_to_the_service_user(module):
    workdir, user, owned = _dockerfile()

    assert user not in {None, "root", "0"}
    assert _inside(workdir / module.DEFAULT_LOCAL_DIR, owned)


def test_the_application_source_stays_root_owned():
    workdir, _user, owned = _dockerfile()

    assert not any(path == workdir or path in workdir.parents for path in owned)


def _image_tree(tmp_path, writable):
    """An application directory where only `writable` paths take writes."""
    app = tmp_path / "app"
    app.mkdir()
    for relative in writable:
        (app / relative).mkdir(parents=True, exist_ok=True)
    readonly = [app] + [
        parent
        for relative in writable
        for parent in (app / relative).parents
        if app in parent.parents
    ]
    for directory in readonly:
        directory.chmod(0o555)
    return app, readonly


def _scheduled_run(app, monkeypatch):
    """The scheduled job's real resolver and downloader, with a fake Datahub."""
    monkeypatch.chdir(app)
    monkeypatch.delenv("SWISSELDEX_SDAT_DIR", raising=False)
    client = FakeFTP({"delivery.xml": (SDAT_XML, "20260807120000")})

    def fetch(directory):
        config = sdat_datahub.DatahubConfig(
            user="test", password="test", local_dir=directory
        )
        return sdat_datahub.fetch_latest(config, client=client)

    return sdat_ingestion.run(
        "dietikon",
        {"max_attempts": 1},
        store=_Store(),
        fetcher=fetch,
        importer=lambda _directory: {
            "files_imported": 1,
            "files_existing": 0,
            "readings_imported": 96,
        },
        sleeper=lambda _seconds: None,
    )


class _Store:
    def acquire(self, territory):
        return True

    def release(self, territory):
        pass

    def record(self, territory, report):
        pass


requires_unprivileged = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)


@requires_unprivileged
def test_the_service_user_downloads_a_synthetic_delivery(tmp_path, monkeypatch):
    workdir, _user, owned = _dockerfile()
    writable = [path.relative_to(workdir) for path in owned if workdir in path.parents]
    app, readonly = _image_tree(tmp_path, writable)
    try:
        result = _scheduled_run(app, monkeypatch)
        delivered = app / "data" / "sdat" / "dietikon" / "delivery.xml"

        assert result["status"] == "success"
        assert result["downloaded_files"] == 1
        assert delivered.read_bytes() == SDAT_XML
    finally:
        for directory in readonly:
            directory.chmod(0o755)


@requires_unprivileged
def test_a_read_only_application_directory_cannot_store_a_delivery(
    tmp_path, monkeypatch
):
    """The layout above is what makes the download work, not the test host."""
    app, readonly = _image_tree(tmp_path, [])
    try:
        result = _scheduled_run(app, monkeypatch)

        assert result["status"] != "success"
        assert not (app / "data").exists()
    finally:
        for directory in readonly:
            directory.chmod(0o755)
