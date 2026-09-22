import json

import pytest

from otdb_downloader.build import build
from otdb_downloader.downloader import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    DownloadAborted,
    DownloadConfig,
    Downloader,
    build_index,
)
from otdb_downloader.store import DataStore, UnrecognisedOutputDir

from conftest import FakeOpenTDB, encode_record, FakeResponse, make_categories, make_question


def run(api, store, make_client, **cfg):
    client = make_client(api, max_retries=cfg.pop("max_retries", 3))
    return Downloader(client, store, DownloadConfig(**cfg)).run()


def stored_counts(store):
    return {cid: len(fps) for cid, fps in build_index(store).items()}


def raw_files(store):
    return sorted(p for p in store.raw_dir.rglob("batch_*.json"))


def test_full_download_all_categories(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 120, 10: 7, 11: 50}))
    summary = run(api, store, make_client)
    assert summary.outcome == "complete"
    assert stored_counts(store) == {9: 120, 10: 7, 11: 50}
    state = json.loads(store.state_path.read_text())
    assert all(c["status"] == STATUS_COMPLETE for c in state["categories"].values())
    assert api.rate_violations == 0
    # categories discovered dynamically and snapshotted verbatim
    assert store.latest_categories_snapshot() is not None


def test_complete_record_is_preserved_verbatim(clock, make_client, store):
    cats = make_categories({9: 3})
    api = FakeOpenTDB(clock, cats)
    run(api, store, make_client)
    _, doc = next(store.iter_raw_batches())
    assert doc["response"]["results"][0] == encode_record(cats[9]["questions"][0])
    assert doc["request"]["params"]["encode"] == "url3986"
    assert "token" not in doc["request"]["params"]


def test_request_size_shrinks_and_category_exhausts_with_code_4(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 57}))
    api.insufficient_code = 4
    api.categories[9]["reported"] = 100  # API over-reports: only 57 retrievable
    summary = run(api, store, make_client)
    assert stored_counts(store) == {9: 57}
    assert summary.categories[9]["status"] == STATUS_COMPLETE
    amounts = [c["params"]["amount"] for c in api.question_calls()]
    assert amounts[0] == 50
    assert 1 in amounts  # probed down to a single question before declaring exhaustion
    assert len(amounts) < 15


def test_reported_total_lower_than_actual_still_collects_everything(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 60}))
    api.categories[9]["reported"] = 40
    run(api, store, make_client)
    assert stored_counts(store) == {9: 60}


def test_reported_zero_terminates(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 0}))
    summary = run(api, store, make_client)
    assert summary.categories[9]["status"] == STATUS_COMPLETE
    assert len(api.question_calls()) <= 5


def test_token_expiry_mid_category_recovers_without_duplicates(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 150}))
    state = {"n": 0}

    def expire_after_two(endpoint, params):
        if endpoint == "api.php":
            state["n"] += 1
            if state["n"] == 3:
                api.expire_all_tokens()

    api.on_request = expire_after_two
    summary = run(api, store, make_client)
    assert summary.categories[9]["status"] == STATUS_COMPLETE
    assert stored_counts(store) == {9: 150}
    assert api.token_counter == 2
    report = build(store)
    assert report["totals"]["duplicate_records_removed"] == 0
    assert report["totals"]["unique_records"] == 150


def test_repeated_token_rejection_is_bounded(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 20, 10: 5}))
    api.fail("api.php", *["code3"] * 50)
    summary = run(api, store, make_client, max_token_renewals=2, categories=[9])
    assert summary.categories[9]["status"] == STATUS_FAILED
    assert len(api.question_calls()) == 3


def test_invalid_parameter_fails_category_and_continues(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 5, 10: 5}))
    api.fail("api.php", "code2")
    summary = run(api, store, make_client)
    assert summary.categories[9]["status"] == STATUS_FAILED
    assert summary.categories[10]["status"] == STATUS_COMPLETE
    assert summary.outcome == "completed_with_problems"


def test_network_outage_aborts_then_resume_completes(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 120, 10: 30}))
    state = {"n": 0}

    def outage(endpoint, params):
        if endpoint == "api.php":
            state["n"] += 1
            if state["n"] == 2:
                api.fail("api.php", *["network"] * 20)

    api.on_request = outage
    with pytest.raises(DownloadAborted):
        run(api, store, make_client, max_retries=2)
    # the first batch (50) was saved before the outage
    assert stored_counts(store) == {9: 50}
    files_before = {p: p.read_bytes() for p in raw_files(store)}

    api.on_request = None
    api.failures.clear()
    summary = run(api, store, make_client)
    assert summary.outcome == "complete"
    assert stored_counts(store) == {9: 120, 10: 30}
    # resumed with the saved token: no batch needed twice
    assert api.token_counter == 1
    # previously written raw files are untouched
    for p, content in files_before.items():
        assert p.read_bytes() == content
    assert build(store)["totals"]["duplicate_records_removed"] == 0


def test_keyboard_interrupt_saves_progress_and_resumes(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 100}))
    state = {"n": 0}

    def interrupt(endpoint, params):
        if endpoint == "api.php":
            state["n"] += 1
            if state["n"] == 2:
                raise KeyboardInterrupt

    api.on_request = interrupt
    with pytest.raises(KeyboardInterrupt):
        run(api, store, make_client)
    saved = json.loads(store.state_path.read_text())
    assert saved["runs"][-1]["outcome"] == "interrupted"
    assert stored_counts(store) == {9: 50}
    api.on_request = None
    run(api, store, make_client)
    assert stored_counts(store) == {9: 100}


def test_crash_between_raw_write_and_state_save_is_recovered(clock, make_client, store, monkeypatch):
    """Raw batch written but state not updated: resume must not duplicate."""
    api = FakeOpenTDB(clock, make_categories({9: 100}))
    client = make_client(api)
    d = Downloader(client, store, DownloadConfig())
    original_write = store.write_raw_batch
    calls = {"n": 0}

    def write_then_crash(*a, **kw):
        rel = original_write(*a, **kw)
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt  # simulate power loss right after the raw file lands
        return rel

    monkeypatch.setattr(store, "write_raw_batch", write_then_crash)
    with pytest.raises(KeyboardInterrupt):
        d.run()
    monkeypatch.setattr(store, "write_raw_batch", original_write)
    # simulate an old/lost state file as well
    store.state_path.unlink()
    api2_client = make_client(api)
    Downloader(api2_client, store, DownloadConfig()).run()
    assert stored_counts(store) == {9: 100}
    report = build(store)
    assert report["totals"]["unique_records"] == 100
    assert report["totals"]["valid"] == 100


def test_duplicate_records_from_api_are_stored_once(clock, make_client, store):
    cats = make_categories({9: 10})
    # the API itself contains an exact duplicate record
    cats[9]["questions"].append(dict(cats[9]["questions"][0]))
    api = FakeOpenTDB(clock, cats)
    run(api, store, make_client)
    assert stored_counts(store) == {9: 10}
    report = build(store)
    assert report["totals"]["unique_records"] == 10
    assert report["totals"]["duplicate_records_removed"] == 1


def test_api_returning_only_repeats_stops_via_stale_limit(clock, make_client, store):
    """A misbehaving API that ignores the token must not cause an infinite loop."""
    cats = make_categories({9: 10})
    api = FakeOpenTDB(clock, cats)
    fixed = FakeResponse(payload={"response_code": 0,
                                  "results": [encode_record(q) for q in cats[9]["questions"][:10]]})
    api.fail("api.php", *[fixed] * 1000)
    summary = run(api, store, make_client, batch_size=10, max_stale_batches=3)
    assert summary.categories[9]["status"] == STATUS_INCOMPLETE
    assert stored_counts(store) == {9: 10}
    assert len(api.question_calls()) == 1 + 3  # first batch, then the stale allowance


def test_request_cap_prevents_runaway(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 500}))
    summary = run(api, store, make_client, max_requests_per_category=4)
    assert summary.categories[9]["status"] == STATUS_INCOMPLETE
    assert len(api.question_calls()) == 4
    # rerun continues where it left off
    run(api, store, make_client)
    assert stored_counts(store) == {9: 500}


def test_malformed_responses_abort_after_bounded_retries(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    api.fail("api.php", *["badjson"] * 100)
    with pytest.raises(DownloadAborted):
        run(api, store, make_client, max_retries=2)
    assert len(api.question_calls()) == 3


def test_rate_limit_responses_are_retried_not_skipped(clock, make_client, store):
    """The original script skipped a whole category on any non-results response."""
    api = FakeOpenTDB(clock, make_categories({9: 60}))
    api.fail("api.php", "code5", "http429", "code5")
    summary = run(api, store, make_client, max_retries=5)
    assert summary.categories[9]["status"] == STATUS_COMPLETE
    assert stored_counts(store) == {9: 60}
    assert api.rate_violations == 0


def test_max_batches_pauses_and_resumes(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 120, 10: 20}))
    summary = run(api, store, make_client, max_batches=2)
    assert summary.outcome == "partial"
    assert stored_counts(store) == {9: 100}
    summary = run(api, store, make_client)
    assert summary.outcome == "complete"
    assert stored_counts(store) == {9: 120, 10: 20}


def test_completed_categories_are_skipped_on_rerun(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 20}))
    run(api, store, make_client)
    before = len(api.question_calls())
    run(api, store, make_client)
    assert len(api.question_calls()) == before


def test_update_mode_collects_only_new_questions(clock, make_client, store):
    cats = make_categories({9: 30})
    api = FakeOpenTDB(clock, cats)
    run(api, store, make_client)
    n_files = len(raw_files(store))
    cats[9]["questions"].extend(make_question("Category 9", 1000 + i) for i in range(5))
    summary = run(api, store, make_client, update=True)
    assert summary.new_records == 5
    assert stored_counts(store) == {9: 35}
    assert len(raw_files(store)) == n_files + 1


def test_category_filter_and_unknown_ids(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 5, 10: 5}))
    run(api, store, make_client, categories=[10])
    assert stored_counts(store) == {10: 5}
    with pytest.raises(ValueError):
        run(api, store, make_client, categories=[99])


def test_refuses_foreign_non_empty_output_dir(clock, make_client, tmp_path):
    target = tmp_path / "somewhere"
    target.mkdir()
    (target / "precious.csv").write_text("do not touch")
    api = FakeOpenTDB(clock, make_categories({9: 5}))
    with pytest.raises(UnrecognisedOutputDir):
        run(api, DataStore(target), make_client)
    assert (target / "precious.csv").read_text() == "do not touch"
    assert api.calls == []


def test_whole_run_respects_rate_limit(clock, make_client, store):
    api = FakeOpenTDB(clock, make_categories({9: 73, 10: 12, 11: 101}))
    api.insufficient_code = 4
    api.fail("api.php", "http500", "code5")
    run(api, store, make_client)
    assert api.rate_violations == 0
    times = [c["at"] for c in api.calls]
    assert all(b - a >= 5.0 for a, b in zip(times, times[1:]))
