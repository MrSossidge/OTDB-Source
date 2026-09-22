import json

import pytest

from otdb_downloader import cli
from otdb_downloader.store import DataStore, StoreLocked, atomic_write_json, exclusive_write_json

from conftest import FakeOpenTDB, make_categories


def test_exclusive_write_never_overwrites(tmp_path):
    p = tmp_path / "raw.json"
    exclusive_write_json(p, {"a": 1})
    with pytest.raises(FileExistsError):
        exclusive_write_json(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [x.name for x in tmp_path.iterdir()] == ["raw.json"]  # no temp files left


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 2}
    assert [x.name for x in tmp_path.iterdir()] == ["state.json"]


def test_batch_numbering_skips_existing_files(tmp_path):
    s = DataStore(tmp_path / "d")
    s.init()
    first = s.write_raw_batch(9, {"amount": 1}, {"response_code": 0, "results": []})
    second = s.write_raw_batch(9, {"amount": 1}, {"response_code": 0, "results": []})
    assert first.endswith("batch_00001.json") and second.endswith("batch_00002.json")


def test_lock_prevents_concurrent_runs(tmp_path):
    s = DataStore(tmp_path / "d")
    s.init()
    s.acquire_lock()
    with pytest.raises(StoreLocked):
        s.acquire_lock()
    s.acquire_lock(break_lock=True)
    s.release_lock()


def test_categories_snapshot_only_written_when_changed(tmp_path):
    s = DataStore(tmp_path / "d")
    s.init()
    payload = {"trivia_categories": [{"id": 9, "name": "General Knowledge"}]}
    assert s.save_categories_snapshot(payload) is not None
    assert s.save_categories_snapshot(payload) is None


# ------------------------------------------------------------------- CLI
@pytest.fixture
def fake_cli(monkeypatch, clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 30, 10: 8}, names={9: "General Knowledge", 10: "Entertainment: Books"}))

    def client_factory(args):
        return make_client(api, max_retries=args.max_retries)

    monkeypatch.setattr(cli, "_client", client_factory)
    return api


def test_cli_download_build_status(fake_cli, tmp_path, capsys):
    out = tmp_path / "data"
    assert cli.main(["download", "-o", str(out)]) == cli.EXIT_OK
    for rel in ["state.json", "clean/questions.json", "export/portapak-questions.json",
                "export/portapak-questions.d.ts", "reports/validation-report.json",
                "reports/validation-report.md", "download.log"]:
        assert (out / rel).exists(), rel
    assert not (out / ".download.lock").exists()
    assert cli.main(["status", "-o", str(out)]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "Total unique records stored: 38" in text
    assert cli.main(["build", "-o", str(out)]) == cli.EXIT_OK


def test_cli_rejects_min_interval_below_limit(fake_cli, tmp_path):
    assert cli.main(["download", "-o", str(tmp_path / "d"), "--min-interval", "1"]) == cli.EXIT_USAGE


def test_cli_rejects_bad_batch_size(fake_cli, tmp_path):
    assert cli.main(["download", "-o", str(tmp_path / "d"), "--batch-size", "51"]) == cli.EXIT_USAGE


def test_cli_refuses_foreign_directory(fake_cli, tmp_path):
    (tmp_path / "existing.csv").write_text("x")
    assert cli.main(["download", "-o", str(tmp_path)]) == cli.EXIT_USAGE
    assert fake_cli.calls == []


def test_cli_network_abort_exit_code_and_resume(fake_cli, tmp_path):
    out = tmp_path / "data"
    fake_cli.fail("api.php", *["network"] * 50)
    assert cli.main(["download", "-o", str(out), "--max-retries", "1"]) == cli.EXIT_ABORTED
    fake_cli.failures.clear()
    assert cli.main(["download", "-o", str(out)]) == cli.EXIT_OK
    report = json.loads((out / "reports" / "validation-report.json").read_text())
    assert report["totals"]["valid"] == 38


def test_cli_build_on_non_store_dir_fails(tmp_path):
    assert cli.main(["build", "-o", str(tmp_path)]) == cli.EXIT_USAGE
