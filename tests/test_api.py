import pytest

from otdb_downloader.api import (
    ClientConfig,
    FatalAPIError,
    OpenTDBClient,
    RateLimiter,
    RetriesExhausted,
)

from conftest import FakeOpenTDB, FakeResponse, make_categories


def test_rate_limiter_spaces_requests(clock):
    rl = RateLimiter(5.0, clock=clock.monotonic, sleep=clock.sleep)
    rl.wait()
    clock.now += 1.0
    slept = rl.wait()
    assert slept == pytest.approx(4.0)
    rl.wait()
    assert clock.sleeps == [pytest.approx(4.0), pytest.approx(5.0)]


def test_min_interval_below_documented_limit_is_rejected():
    with pytest.raises(ValueError):
        OpenTDBClient(ClientConfig(min_interval=4.9), session=object())


def test_client_never_violates_rate_limit(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 120}))
    client = make_client(api)
    tok = client.request_token()
    for _ in range(3):
        client.fetch_questions(9, 40, tok)
    client.get_categories()
    assert api.rate_violations == 0
    assert len(api.calls) == 5


@pytest.mark.parametrize("failure", ["network", "timeout", "http500", "badjson", "code5", "no_results_field"])
def test_transient_failures_are_retried(clock, make_client, failure):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=3, backoff_base=5.0)
    api.fail("api.php", failure, failure)
    batch = client.fetch_questions(9, 5, None)
    assert batch.response_code == 0
    assert len(batch.results) == 5
    assert len(api.question_calls()) == 3
    assert api.rate_violations == 0


def test_backoff_grows_exponentially_and_is_capped(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=4, backoff_base=5.0, backoff_max=12.0)
    api.fail("api.php", "http500", "http500", "http500", "http500")
    client.fetch_questions(9, 1, None)
    times = [c["at"] for c in api.question_calls()]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps == [pytest.approx(5.0), pytest.approx(10.0), pytest.approx(12.0), pytest.approx(12.0)]


def test_http_429_honours_retry_after(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=2, backoff_base=5.0)
    api.fail("api.php", "http429")
    client.fetch_questions(9, 1, None)
    t = [c["at"] for c in api.question_calls()]
    assert t[1] - t[0] >= 30.0


def test_retries_are_bounded(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=2)
    api.fail("api.php", *["network"] * 10)
    with pytest.raises(RetriesExhausted):
        client.fetch_questions(9, 1, None)
    assert len(api.question_calls()) == 3


def test_non_transient_http_error_is_not_retried(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=5)
    api.fail("api.php", FakeResponse(status_code=404, text="missing"))
    with pytest.raises(FatalAPIError):
        client.fetch_questions(9, 1, None)
    assert len(api.question_calls()) == 1


def test_token_is_not_recorded_in_public_params(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api)
    tok = client.request_token()
    batch = client.fetch_questions(9, 2, tok)
    assert "token" not in batch.params
    assert batch.params == {"amount": 2, "category": 9, "encode": "url3986"}


def test_malformed_category_list_is_retried_then_fails(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api, max_retries=1)
    bad = FakeResponse(payload={"trivia_categories": [{"id": "nine"}]})
    api.fail("api_category.php", bad, bad)
    with pytest.raises(RetriesExhausted):
        client.get_categories()


def test_reset_token_falls_back_to_new_token_when_missing(clock, make_client):
    api = FakeOpenTDB(clock, make_categories({9: 10}))
    client = make_client(api)
    new = client.reset_token("does-not-exist")
    assert new in api.tokens
