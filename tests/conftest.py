"""Test doubles: a fake clock and a stateful fake OpenTDB API.

No test in this suite touches the network. ``FakeOpenTDB`` implements the
subset of the OpenTDB API used by the downloader (categories, counts, tokens,
questions with url3986 encoding) and enforces the 5-second rate limit against
the fake clock, so tests can assert that the client never violates it.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional
from urllib.parse import quote

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otdb_downloader.api import ClientConfig, OpenTDBClient  # noqa: E402
from otdb_downloader.store import DataStore  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self._payload = payload
        self._text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if self._text is not None:
            return json.loads(self._text)  # raises ValueError (JSONDecodeError) if malformed
        return self._payload


def enc(s: str) -> str:
    return quote(s, safe="-_.~")


def make_question(category: str, n: int, *, qtype: str = "multiple", difficulty: str = "easy",
                  question: Optional[str] = None) -> Dict[str, Any]:
    """A decoded question record (the fake API encodes it on the way out)."""
    if qtype == "boolean":
        correct, incorrect = ("True", ["False"]) if n % 2 else ("False", ["True"])
    else:
        correct, incorrect = f"Answer {n}", [f"Wrong {n}a", f"Wrong {n}b", f"Wrong {n}c"]
    return {
        "type": qtype,
        "difficulty": difficulty,
        "category": category,
        "question": question or f"{category} question number {n}?",
        "correct_answer": correct,
        "incorrect_answers": incorrect,
    }


def encode_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in rec.items():
        if isinstance(v, str):
            out[k] = enc(v)
        elif isinstance(v, list):
            out[k] = [enc(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


class FakeOpenTDB:
    """Stateful fake of opentdb.com, usable as a ``requests.Session`` stand-in."""

    MIN_INTERVAL = 5.0

    def __init__(self, clock: FakeClock, categories: Dict[int, Dict[str, Any]]):
        """categories: id -> {"name": str, "questions": [decoded records], "reported": Optional[int]}"""
        self.clock = clock
        self.categories = categories
        self.tokens: Dict[str, set] = {}
        self.token_counter = 0
        self.calls: List[Dict[str, Any]] = []
        self.rate_violations = 0
        self.last_request_at: Optional[float] = None
        # queued failure injections per endpoint ("*" = any endpoint)
        self.failures: Dict[str, Deque[Any]] = defaultdict(deque)
        self.insufficient_code = 1  # code returned when amount > remaining (token not empty)
        self.headers: Dict[str, str] = {}
        self.on_request: Optional[Callable[[str, Dict[str, Any]], None]] = None

    # -- helpers for tests
    def fail(self, endpoint: str, *actions: Any) -> None:
        self.failures[endpoint].extend(actions)

    def expire_all_tokens(self) -> None:
        self.tokens.clear()

    def question_calls(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["endpoint"] == "api.php"]

    # -- requests.Session interface
    def get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: Any = None) -> FakeResponse:
        assert timeout is not None, "every request must carry a timeout"
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(params or {})
        now = self.clock.monotonic()
        if self.last_request_at is not None and now - self.last_request_at < self.MIN_INTERVAL - 1e-9:
            self.rate_violations += 1
        self.last_request_at = now
        self.calls.append({"endpoint": endpoint, "params": params, "at": now})
        if self.on_request:
            self.on_request(endpoint, params)

        for key in (endpoint, "*"):
            if self.failures[key]:
                action = self.failures[key].popleft()
                resp = self._failure(action)
                if resp is not None:
                    return resp

        if endpoint == "api_category.php":
            return FakeResponse(payload={"trivia_categories": [
                {"id": cid, "name": c["name"]} for cid, c in sorted(self.categories.items())]})
        if endpoint == "api_count.php":
            cid = int(params["category"])
            c = self.categories[cid]
            total = c.get("reported", len(c["questions"]))
            return FakeResponse(payload={"category_id": cid, "category_question_count": {
                "total_question_count": total, "total_easy_question_count": total,
                "total_medium_question_count": 0, "total_hard_question_count": 0}})
        if endpoint == "api_token.php":
            if params.get("command") == "request":
                self.token_counter += 1
                tok = f"tok{self.token_counter}"
                self.tokens[tok] = set()
                return FakeResponse(payload={"response_code": 0, "response_message": "Token Generated Successfully!",
                                             "token": tok})
            if params.get("command") == "reset":
                tok = params.get("token")
                if tok not in self.tokens:
                    return FakeResponse(payload={"response_code": 3})
                self.tokens[tok] = set()
                return FakeResponse(payload={"response_code": 0, "token": tok})
        if endpoint == "api.php":
            return self._questions(params)
        return FakeResponse(status_code=404, text="not found")

    def _failure(self, action: Any) -> Optional[FakeResponse]:
        if action == "network":
            raise requests.ConnectionError("simulated connection failure")
        if action == "timeout":
            raise requests.Timeout("simulated timeout")
        if action == "http500":
            return FakeResponse(status_code=500, text="oops")
        if action == "http429":
            return FakeResponse(status_code=429, text="slow down", headers={"Retry-After": "30"})
        if action == "badjson":
            return FakeResponse(text="<html>not json</html>")
        if action == "code5":
            return FakeResponse(payload={"response_code": 5, "results": []})
        if action == "code3":
            return FakeResponse(payload={"response_code": 3, "results": []})
        if action == "code2":
            return FakeResponse(payload={"response_code": 2, "results": []})
        if action == "no_results_field":
            return FakeResponse(payload={"response_code": 0})
        if action == "interrupt":
            raise KeyboardInterrupt()
        if isinstance(action, FakeResponse):
            return action
        if callable(action):
            return action()
        return None

    def _questions(self, params: Dict[str, Any]) -> FakeResponse:
        amount = int(params["amount"])
        cid = int(params["category"])
        if cid not in self.categories or not 1 <= amount <= 50:
            return FakeResponse(payload={"response_code": 2, "results": []})
        assert params.get("encode") == "url3986"
        questions = self.categories[cid]["questions"]
        token = params.get("token")
        if token is not None and token not in self.tokens:
            return FakeResponse(payload={"response_code": 3, "results": []})
        seen = self.tokens[token] if token else set()
        available = [i for i in range(len(questions)) if (cid, i) not in seen]
        if not available:
            return FakeResponse(payload={"response_code": 4 if token else 1, "results": []})
        if amount > len(available):
            return FakeResponse(payload={"response_code": self.insufficient_code if token else 1, "results": []})
        chosen = available[:amount]
        if token:
            seen.update((cid, i) for i in chosen)
        return FakeResponse(payload={"response_code": 0,
                                     "results": [encode_record(questions[i]) for i in chosen]})


def make_categories(sizes: Dict[int, int], names: Optional[Dict[int, str]] = None) -> Dict[int, Dict[str, Any]]:
    names = names or {}
    cats = {}
    for cid, size in sizes.items():
        name = names.get(cid, f"Category {cid}")
        qs = []
        for n in range(size):
            qtype = "boolean" if n % 5 == 4 else "multiple"
            diff = ("easy", "medium", "hard")[n % 3]
            qs.append(make_question(name, n, qtype=qtype, difficulty=diff))
        cats[cid] = {"name": name, "questions": qs}
    return cats


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(clock):
    def factory(api: FakeOpenTDB, **cfg) -> OpenTDBClient:
        config = ClientConfig(base_url="https://opentdb.test", **cfg)
        return OpenTDBClient(config, session=api, clock=clock.monotonic, sleep=clock.sleep)
    return factory


@pytest.fixture
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "data")
