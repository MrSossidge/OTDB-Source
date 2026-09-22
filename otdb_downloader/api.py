"""Thin, defensive client for the Open Trivia Database HTTP API.

Responsibilities:

* enforce the documented rate limit (one request per IP every 5 seconds);
* apply connect/read timeouts to every request;
* retry transient failures (network errors, HTTP 429/5xx, malformed JSON,
  API response code 5) a bounded number of times with exponential backoff;
* return non-transient API response codes (0-4) to the caller, which owns
  the download policy.

The HTTP session, clock and sleep function are injectable so the whole client
can be exercised offline in tests.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from . import OPENTDB_BASE_URL, OPENTDB_MAX_AMOUNT, OPENTDB_MIN_INTERVAL, __version__

log = logging.getLogger(__name__)

# Documented OpenTDB response codes (https://opentdb.com/api_config.php)
CODE_SUCCESS = 0
CODE_NO_RESULTS = 1
CODE_INVALID_PARAMETER = 2
CODE_TOKEN_NOT_FOUND = 3
CODE_TOKEN_EMPTY = 4
CODE_RATE_LIMIT = 5

RESPONSE_CODE_NAMES = {
    CODE_SUCCESS: "success",
    CODE_NO_RESULTS: "no results",
    CODE_INVALID_PARAMETER: "invalid parameter",
    CODE_TOKEN_NOT_FOUND: "token not found",
    CODE_TOKEN_EMPTY: "token empty",
    CODE_RATE_LIMIT: "rate limit",
}


class OpenTDBError(Exception):
    """Base class for client errors."""


class TransientError(OpenTDBError):
    """A failure that may succeed if retried."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class RetriesExhausted(OpenTDBError):
    """A request kept failing after the configured number of retries."""


class FatalAPIError(OpenTDBError):
    """A failure that retrying will not fix (e.g. HTTP 404, bad token request)."""


@dataclass
class ClientConfig:
    base_url: str = OPENTDB_BASE_URL
    min_interval: float = OPENTDB_MIN_INTERVAL
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    max_retries: int = 5
    backoff_base: float = 5.0
    backoff_max: float = 120.0
    user_agent: str = f"otdb-downloader-portapak/{__version__} (+https://github.com/MrSossidge/OTDB-Source)"

    def validate(self) -> None:
        if self.min_interval < OPENTDB_MIN_INTERVAL:
            raise ValueError(
                f"min_interval must be at least {OPENTDB_MIN_INTERVAL}s "
                "(OpenTDB allows one request per IP every 5 seconds)"
            )
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.backoff_base < 0 or self.backoff_max < 0:
            raise ValueError("backoff values must be >= 0")


class RateLimiter:
    """Guarantees a minimum gap between consecutive requests.

    ``penalise`` pushes the next permitted request further into the future,
    which is how backoff after a failure is applied.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: Optional[float] = None

    def wait(self) -> float:
        """Block until a request is allowed; returns seconds slept."""
        now = self._clock()
        slept = 0.0
        if self._next_allowed is not None and now < self._next_allowed:
            slept = self._next_allowed - now
            self._sleep(slept)
        self._next_allowed = self._clock() + self.min_interval
        return slept

    def penalise(self, seconds: float) -> None:
        target = self._clock() + max(seconds, self.min_interval)
        if self._next_allowed is None or target > self._next_allowed:
            self._next_allowed = target

    def seconds_until_next(self) -> float:
        if self._next_allowed is None:
            return 0.0
        return max(0.0, self._next_allowed - self._clock())


@dataclass
class QuestionBatch:
    response_code: int
    results: List[Any]
    payload: Dict[str, Any]
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def code_name(self) -> str:
        return RESPONSE_CODE_NAMES.get(self.response_code, f"unknown ({self.response_code})")


class OpenTDBClient:
    def __init__(
        self,
        config: Optional[ClientConfig] = None,
        session: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config or ClientConfig()
        self.config.validate()
        self.session = session if session is not None else requests.Session()
        try:
            self.session.headers.update({"User-Agent": self.config.user_agent})
        except AttributeError:  # simple fakes in tests may not carry headers
            pass
        self.limiter = RateLimiter(self.config.min_interval, clock=clock, sleep=sleep)
        self.request_count = 0

    # ------------------------------------------------------------------ core
    def _url(self, endpoint: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{endpoint}"

    def _attempt(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.limiter.wait()
        self.request_count += 1
        try:
            resp = self.session.get(
                self._url(endpoint),
                params=params,
                timeout=(self.config.connect_timeout, self.config.read_timeout),
            )
        except requests.Timeout as exc:
            raise TransientError(f"timeout contacting {endpoint}: {exc}") from exc
        except requests.RequestException as exc:
            raise TransientError(f"network error contacting {endpoint}: {exc}") from exc

        status = resp.status_code
        if status == 429:
            retry_after = _parse_retry_after(resp)
            raise TransientError("HTTP 429 Too Many Requests", retry_after=retry_after)
        if 500 <= status < 600:
            raise TransientError(f"HTTP {status} server error")
        if status != 200:
            raise FatalAPIError(f"HTTP {status} from {endpoint}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise TransientError(f"malformed JSON from {endpoint}") from exc
        if not isinstance(data, dict):
            raise TransientError(f"unexpected JSON shape from {endpoint}: {type(data).__name__}")

        code = data.get("response_code")
        if code == CODE_RATE_LIMIT:
            raise TransientError("OpenTDB response code 5 (rate limit)")
        return data

    def get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None,
                 validator: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """GET an endpoint with bounded retries. ``validator`` may raise
        TransientError to treat a structurally bad payload as retryable."""
        params = dict(params or {})
        attempts = self.config.max_retries + 1
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                data = self._attempt(endpoint, params)
                if validator is not None:
                    validator(data)
                return data
            except TransientError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                delay = min(self.config.backoff_max, self.config.backoff_base * (2 ** attempt))
                if exc.retry_after is not None:
                    delay = max(delay, min(exc.retry_after, self.config.backoff_max))
                log.warning(
                    "%s failed (%s); retry %d/%d in %.1fs",
                    endpoint, exc, attempt + 1, self.config.max_retries, delay,
                )
                self.limiter.penalise(delay)
        raise RetriesExhausted(
            f"{endpoint} failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    # ------------------------------------------------------------ endpoints
    def get_categories(self) -> Dict[str, Any]:
        """Return the verbatim api_category.php payload (validated)."""

        def check(data: Dict[str, Any]) -> None:
            cats = data.get("trivia_categories")
            if not isinstance(cats, list) or not cats:
                raise TransientError("category list missing or empty")
            for c in cats:
                if not isinstance(c, dict) or not isinstance(c.get("id"), int) or not isinstance(c.get("name"), str):
                    raise TransientError(f"malformed category entry: {c!r}")

        return self.get_json("api_category.php", validator=check)

    def get_category_count(self, category_id: int) -> Dict[str, int]:
        def check(data: Dict[str, Any]) -> None:
            counts = data.get("category_question_count")
            if not isinstance(counts, dict) or not isinstance(counts.get("total_question_count"), int):
                raise TransientError("category count missing or malformed")

        data = self.get_json("api_count.php", {"category": category_id}, validator=check)
        return data["category_question_count"]

    def request_token(self) -> str:
        data = self.get_json("api_token.php", {"command": "request"})
        token = data.get("token")
        if data.get("response_code") != CODE_SUCCESS or not isinstance(token, str) or not token:
            raise FatalAPIError(f"could not obtain a session token: {data!r}")
        return token

    def reset_token(self, token: str) -> str:
        data = self.get_json("api_token.php", {"command": "reset", "token": token})
        code = data.get("response_code")
        if code == CODE_TOKEN_NOT_FOUND:
            log.info("Token to reset no longer exists; requesting a new one")
            return self.request_token()
        new_token = data.get("token") or token
        if code != CODE_SUCCESS:
            raise FatalAPIError(f"token reset failed: {data!r}")
        return new_token

    def fetch_questions(self, category_id: int, amount: int, token: Optional[str]) -> QuestionBatch:
        if not 1 <= amount <= OPENTDB_MAX_AMOUNT:
            raise ValueError(f"amount must be between 1 and {OPENTDB_MAX_AMOUNT}")
        params: Dict[str, Any] = {"amount": amount, "category": category_id, "encode": "url3986"}
        if token:
            params["token"] = token

        def check(data: Dict[str, Any]) -> None:
            code = data.get("response_code")
            if not isinstance(code, int) or isinstance(code, bool):
                raise TransientError("response_code missing or not an integer")
            if code == CODE_SUCCESS and not isinstance(data.get("results"), list):
                raise TransientError("results missing from successful response")

        data = self.get_json("api.php", params, validator=check)
        results = data.get("results") if isinstance(data.get("results"), list) else []
        public_params = {k: v for k, v in params.items() if k != "token"}
        return QuestionBatch(response_code=data["response_code"], results=results,
                             payload=data, params=public_params)


def _parse_retry_after(resp: Any) -> Optional[float]:
    try:
        value = resp.headers.get("Retry-After")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
