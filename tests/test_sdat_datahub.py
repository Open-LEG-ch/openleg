# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swisseldex Datahub SDAT retrieval (ftpes://datahub.swisseldex.ch).

The connector pulls the SDAT files a VNB drops into our Datahub outbox over
FTP with explicit TLS. Every test here runs against a fake FTP client, so the
suite needs no network, no credentials, and no live Datahub account.
"""

import ftplib
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sdat_datahub

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# === Fake FTP client ===


class FakeFTP:
    """Minimal stand-in for ftplib.FTP_TLS covering the calls we make."""

    def __init__(self, files, *, supports_mlsd=True):
        # files: {name: (payload_bytes, "YYYYMMDDHHMMSS")}
        self.files = dict(files)
        self.supports_mlsd = supports_mlsd
        self.cwd_calls = []
        self.deleted = []
        self.quit_called = False

    def cwd(self, path):
        self.cwd_calls.append(path)

    def mlsd(self, path="", facts=None):
        """List one directory of the tree keyed by relative path."""
        if not self.supports_mlsd:
            raise ftplib.error_perm("500 Unknown command MLSD")
        base = "" if path in ("", ".", "/") else path.strip("/")
        prefix = f"{base}/" if base else ""
        yield ".", {"type": "cdir"}
        seen_dirs = set()
        for name, (payload, modify) in self.files.items():
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix) :]
            if "/" in rest:
                subdir = rest.split("/", 1)[0]
                if subdir not in seen_dirs:
                    seen_dirs.add(subdir)
                    yield subdir, {"type": "dir"}
            else:
                yield (
                    rest,
                    {"type": "file", "size": str(len(payload)), "modify": modify},
                )

    def nlst(self, *args):
        return list(self.files)

    def size(self, name):
        return len(self.files[name][0])

    def sendcmd(self, command):
        if command.startswith("MDTM "):
            return f"213 {self.files[command[5:]][1]}"
        return "200 OK"

    def retrbinary(self, command, callback, blocksize=8192):
        name = command.removeprefix("RETR ")
        payload = self.files[name][0]
        for offset in range(0, len(payload), blocksize):
            callback(payload[offset : offset + blocksize])
        return "226 Transfer complete"

    def delete(self, name):
        self.deleted.append(name)
        del self.files[name]

    def quit(self):
        self.quit_called = True


SDAT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:ValidatedMeteredData_02 xmlns:rsm="http://www.strom.ch/SDAT">
  <rsm:MeteringData><rsm:DocumentID>SDAT-1</rsm:DocumentID></rsm:MeteringData>
</rsm:ValidatedMeteredData_02>
"""


@pytest.fixture
def config(tmp_path):
    return sdat_datahub.DatahubConfig(
        host="datahub.swisseldex.ch",
        port=21,
        user="leg-user",
        password="secret",
        remote_dir="/outbox",
        local_dir=str(tmp_path / "sdat"),
    )


# === Configuration ===


class TestLoadConfig:
    def test_reads_credentials_from_environment(self):
        env = {
            "SWISSELDEX_FTPS_USER": "leg-user",
            "SWISSELDEX_FTPS_PASSWORD": "secret",
            "SWISSELDEX_FTPS_REMOTE_DIR": "/outbox",
            "SWISSELDEX_SDAT_DIR": "/var/data/sdat",
        }
        config = sdat_datahub.load_config(env)
        assert config.host == "datahub.swisseldex.ch"
        assert config.port == 21
        assert config.user == "leg-user"
        assert config.password == "secret"
        assert config.remote_dir == "/outbox"
        assert config.local_dir == "/var/data/sdat"

    def test_missing_credentials_raise_config_error(self):
        with pytest.raises(sdat_datahub.ConfigError):
            sdat_datahub.load_config({"SWISSELDEX_FTPS_USER": "leg-user"})

    def test_password_is_never_in_the_repr(self, config):
        assert "secret" not in repr(config)


# === Listing ===


class TestListRemoteFiles:
    def test_remote_file_normalizes_a_naive_modified_time_to_utc(self):
        naive = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        remote = sdat_datahub.RemoteFile("new.xml", modified=naive)
        older = sdat_datahub.RemoteFile(
            "old.xml", modified=datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
        )

        assert remote.modified == naive.replace(tzinfo=timezone.utc)
        assert sdat_datahub.sort_newest_first([older, remote]) == [remote, older]

    @pytest.mark.parametrize(
        "name",
        ("line\nbreak.xml", "tab\tname.xml", "delete\x7fname.xml"),
    )
    def test_rejects_control_characters_in_remote_names(self, name):
        assert sdat_datahub._is_safe_name(name) is False

    def test_parses_mlsd_facts_and_skips_directories(self):
        client = FakeFTP(
            {
                "a.xml": (SDAT_XML, "20260807120000"),
                "archive/old.xml": (SDAT_XML, "20260101120000"),
            }
        )
        files = sdat_datahub.list_remote_files(client, "/outbox")

        assert client.cwd_calls == ["/outbox"]
        assert [f.name for f in files] == ["a.xml"]
        assert files[0].size == len(SDAT_XML)
        assert files[0].modified == datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_recursive_descends_into_subfolders(self):
        client = FakeFTP(
            {
                "a.xml": (SDAT_XML, "20260807120000"),
                "2026/08/b.xml": (SDAT_XML, "20260808120000"),
            }
        )
        files = sdat_datahub.list_remote_files(client, "/outbox", recursive=True)

        assert [f.path for f in files] == ["2026/08/b.xml", "a.xml"]
        assert [f.name for f in files] == ["b.xml", "a.xml"]

    def test_one_unreadable_subfolder_does_not_lose_the_rest(self):
        client = FakeFTP(
            {
                "a.xml": (SDAT_XML, "20260807120000"),
                "locked/secret.xml": (SDAT_XML, "20260808120000"),
            }
        )
        real_mlsd = client.mlsd

        def mlsd(path="", facts=None):
            if path == "locked":
                raise ftplib.error_perm("550 Permission denied")
            yield from real_mlsd(path=path, facts=facts)

        client.mlsd = mlsd
        files = sdat_datahub.list_remote_files(client, "/outbox", recursive=True)

        assert [f.path for f in files] == ["a.xml"]

    def test_recursive_depth_limit_reports_skipped_subdirectories(self, caplog):
        client = FakeFTP({"deeper/a.xml": (SDAT_XML, "20260807120000")})

        with caplog.at_level("WARNING", logger="sdat_datahub"):
            files = sdat_datahub._walk_mlsd(
                client,
                recursive=True,
                depth=sdat_datahub.MAX_RECURSION_DEPTH,
            )

        assert files == []
        assert "Maximale Tiefe" in caplog.text
        assert "deeper" in caplog.text

    def test_falls_back_to_nlst_when_mlsd_is_unsupported(self):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")}, supports_mlsd=False)
        files = sdat_datahub.list_remote_files(client, "/outbox")

        assert [f.name for f in files] == ["a.xml"]
        assert files[0].size == len(SDAT_XML)
        assert files[0].modified == datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_rejects_names_that_escape_the_download_directory(self):
        client = FakeFTP(
            {
                "../../etc/passwd": (b"root:x:0:0", "20260807120000"),
                "good.xml": (SDAT_XML, "20260807120000"),
            }
        )
        assert [f.name for f in sdat_datahub.list_remote_files(client)] == ["good.xml"]


# === Download ===


class TestFetchLatest:
    def test_downloads_every_remote_file_into_the_local_directory(self, config):
        client = FakeFTP(
            {
                "a.xml": (SDAT_XML, "20260807120000"),
                "b.xml": (SDAT_XML, "20260808120000"),
            }
        )
        result = sdat_datahub.fetch_latest(config, client=client)

        assert sorted(result["downloaded"]) == ["a.xml", "b.xml"]
        assert result["bytes"] == 2 * len(SDAT_XML)
        target = sdat_datahub.Path(config.local_dir) / "a.xml"
        assert target.read_bytes() == SDAT_XML

    def test_recursive_download_mirrors_the_remote_folders(self, config):
        client = FakeFTP({"2026/08/b.xml": (SDAT_XML, "20260808120000")})
        result = sdat_datahub.fetch_latest(config, client=client, recursive=True)

        assert result["downloaded"] == ["2026/08/b.xml"]
        target = sdat_datahub.Path(config.local_dir) / "2026" / "08" / "b.xml"
        assert target.read_bytes() == SDAT_XML

    def test_subfolders_are_left_alone_without_recursive(self, config):
        client = FakeFTP({"2026/08/b.xml": (SDAT_XML, "20260808120000")})
        result = sdat_datahub.fetch_latest(config, client=client)

        assert result["listed"] == 0
        assert result["downloaded"] == []

    def test_skips_files_already_downloaded(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        sdat_datahub.fetch_latest(config, client=client)

        again = sdat_datahub.fetch_latest(config, client=client)
        assert again["downloaded"] == []
        assert again["skipped"] == ["a.xml"]

    def test_force_redownloads_existing_files(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        sdat_datahub.fetch_latest(config, client=client)

        again = sdat_datahub.fetch_latest(config, client=client, force=True)
        assert again["downloaded"] == ["a.xml"]

    def test_limit_keeps_the_newest_files(self, config):
        client = FakeFTP(
            {
                "old.xml": (SDAT_XML, "20260101120000"),
                "new.xml": (SDAT_XML, "20260808120000"),
            }
        )
        result = sdat_datahub.fetch_latest(config, client=client, limit=1)
        assert result["downloaded"] == ["new.xml"]

    def test_limit_zero_downloads_nothing(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260808120000")})

        result = sdat_datahub.fetch_latest(config, client=client, limit=0)

        assert result["pending"] == []
        assert result["downloaded"] == []
        assert not (Path(config.local_dir) / "a.xml").exists()

    def test_since_filters_out_older_files(self, config):
        client = FakeFTP(
            {
                "old.xml": (SDAT_XML, "20260101120000"),
                "new.xml": (SDAT_XML, "20260808120000"),
            }
        )
        result = sdat_datahub.fetch_latest(
            config, client=client, since=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )
        assert result["downloaded"] == ["new.xml"]

    def test_dry_run_writes_nothing(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        result = sdat_datahub.fetch_latest(config, client=client, dry_run=True)

        assert result["pending"] == ["a.xml"]
        assert result["downloaded"] == []
        assert not (sdat_datahub.Path(config.local_dir) / "a.xml").exists()

    def test_keeps_remote_files_by_default(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        sdat_datahub.fetch_latest(config, client=client)
        assert client.deleted == []

    def test_deletes_remote_files_only_when_asked(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        result = sdat_datahub.fetch_latest(config, client=client, delete_remote=True)

        assert client.deleted == ["a.xml"]
        assert result["deleted"] == ["a.xml"]

    def test_a_short_transfer_is_failed_for_both_delete_remote_false_and_true(
        self, config, caplog
    ):
        """A truncated download must be marked failed, target removed, never deleted."""
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})

        def short(command, callback, blocksize=8192):
            callback(SDAT_XML[:10])
            return "226 Transfer complete"

        client.retrbinary = short

        with caplog.at_level("WARNING", logger="sdat_datahub"):
            for delete_remote in (False, True):
                result = sdat_datahub.fetch_latest(
                    config, client=client, delete_remote=delete_remote
                )
                assert result["failed"] == ["a.xml"], f"delete_remote={delete_remote}"
                assert result["downloaded"] == [], f"delete_remote={delete_remote}"
                assert result["bytes"] == 0, f"delete_remote={delete_remote}"
                assert client.deleted == [], f"delete_remote={delete_remote}"
                target = sdat_datahub.Path(config.local_dir) / "a.xml"
                assert not target.exists(), f"delete_remote={delete_remote}"

        assert "unvollständig" in caplog.text

    def test_a_short_forced_retry_preserves_the_existing_complete_file(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        target = Path(config.local_dir) / "a.xml"
        target.parent.mkdir(parents=True)
        target.write_bytes(SDAT_XML)

        def short(command, callback, blocksize=8192):
            callback(b"truncated")
            return "226 Transfer complete"

        client.retrbinary = short
        result = sdat_datahub.fetch_latest(config, client=client, force=True)

        assert result["failed"] == ["a.xml"]
        assert result["downloaded"] == []
        assert target.read_bytes() == SDAT_XML

    def test_a_transfer_without_a_verifiable_remote_size_is_failed(
        self, config, monkeypatch
    ):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})
        remote = sdat_datahub.RemoteFile("a.xml", size=0)
        client.size = lambda path: (_ for _ in ()).throw(
            ftplib.error_perm("SIZE unavailable")
        )
        monkeypatch.setattr(
            sdat_datahub, "list_remote_files", lambda *args, **kwargs: [remote]
        )

        result = sdat_datahub.fetch_latest(config, client=client)

        target = Path(config.local_dir) / "a.xml"
        assert result["failed"] == ["a.xml"]
        assert result["downloaded"] == []
        assert not target.exists()

    def test_unknown_remote_size_never_marks_an_existing_file_complete(self, tmp_path):
        target = tmp_path / "a.xml"
        target.write_bytes(SDAT_XML)
        remote = sdat_datahub.RemoteFile("a.xml", size=0)

        assert sdat_datahub._already_downloaded(target, remote) is False

    def test_repr_and_connected_log_omit_user(self, config, monkeypatch, caplog):
        assert "leg-user" not in repr(config)

        class FakeFTP_TLS:
            def __init__(self, context):
                pass

            def connect(self, *, host, port, timeout):
                return self

            def auth(self):
                return self

            def login(self, *, user, passwd):
                return self

            def prot_p(self):
                return self

            def set_pasv(self, val):
                return self

            def quit(self):
                pass

        monkeypatch.setattr(sdat_datahub, "_SessionReusingFTP_TLS", FakeFTP_TLS)
        with caplog.at_level("INFO", logger="sdat_datahub"):
            sdat_datahub.connect(config)
        assert "leg-user" not in caplog.text
        assert "Verbunden mit" in caplog.text

    def test_a_failed_transfer_leaves_no_partial_file(self, config):
        client = FakeFTP({"a.xml": (SDAT_XML, "20260807120000")})

        def boom(command, callback, blocksize=8192):
            callback(b"<?xml")
            raise ftplib.error_temp("426 Transfer aborted")

        client.retrbinary = boom
        result = sdat_datahub.fetch_latest(config, client=client)

        assert result["downloaded"] == []
        assert result["failed"] == ["a.xml"]
        local = sdat_datahub.Path(config.local_dir)
        assert list(local.glob("*")) == []


# === Repository contract ===


class TestDatahubContract:
    def test_env_example_documents_the_datahub_credentials(self):
        content = Path(PROJECT_ROOT, ".env.example").read_text(encoding="utf-8")
        for key in (
            "SWISSELDEX_FTPS_HOST",
            "SWISSELDEX_FTPS_USER",
            "SWISSELDEX_FTPS_PASSWORD",
            "SWISSELDEX_FTPS_REMOTE_DIR",
            "SWISSELDEX_SDAT_DIR",
        ):
            assert f"{key}=" in content, f"{key} fehlt in .env.example"

    def test_env_example_ships_no_real_datahub_password(self):
        content = Path(PROJECT_ROOT, ".env.example").read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("SWISSELDEX_FTPS_PASSWORD="):
                assert line.strip() == "SWISSELDEX_FTPS_PASSWORD="

    def test_downloaded_sdat_files_stay_out_of_git(self):
        ignored = Path(PROJECT_ROOT, ".gitignore").read_text(encoding="utf-8")
        assert "/data/*" in ignored

    def test_manual_fetch_script_exists_and_is_executable(self):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        assert script.exists()
        assert os.access(script, os.X_OK)

    def test_since_days_zero_remains_an_explicit_filter(self, monkeypatch):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        namespace = runpy.run_path(str(script))
        captured = {}
        config = sdat_datahub.DatahubConfig(
            host="example.invalid",
            port=21,
            user="test",
            password="test",
            remote_dir="/outbox",
            local_dir="data/sdat",
        )

        monkeypatch.setattr(namespace["sdat_datahub"], "load_config", lambda: config)

        def fetch_latest(_config, **kwargs):
            captured.update(kwargs)
            return {
                "listed": 0,
                "pending": [],
                "downloaded": [],
                "skipped": [],
                "failed": [],
                "deleted": [],
                "bytes": 0,
            }

        monkeypatch.setattr(namespace["sdat_datahub"], "fetch_latest", fetch_latest)

        assert namespace["main"](["--since-days", "0", "--list"]) == 0
        assert captured["since"] is not None

    def test_since_days_rejects_negative_values(self):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        namespace = runpy.run_path(str(script))

        with pytest.raises(SystemExit):
            namespace["build_parser"]().parse_args(["--since-days", "-1"])

    def test_since_days_accepts_zero(self):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        namespace = runpy.run_path(str(script))

        args = namespace["build_parser"]().parse_args(["--since-days", "0"])

        assert args.since_days == 0

    def test_limit_rejects_negative_values(self):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        namespace = runpy.run_path(str(script))

        with pytest.raises(SystemExit):
            namespace["build_parser"]().parse_args(["--limit", "-1"])

    def test_limit_accepts_zero(self):
        script = Path(PROJECT_ROOT, "scripts", "fetch_sdat.py")
        namespace = runpy.run_path(str(script))

        args = namespace["build_parser"]().parse_args(["--limit", "0"])

        assert args.limit == 0
