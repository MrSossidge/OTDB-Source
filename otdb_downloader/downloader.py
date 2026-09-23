"""Download orchestration: categories, tokens, batching, resumption.

Policy summary
--------------
* Categories are discovered from ``api_category.php`` on every run.
* One session token is shared by the whole run and persisted in state so an
  interrupted run resumes with the same token (OpenTDB deletes tokens after
  6 hours of inactivity; an expired token is detected via code 3 and replaced).
* Each successful batch that contains at least one new record is written to a
  new, never-overwritten raw file, then state is saved atomically.
* Response codes 1 (no results) and 4 (token empty) mean "fewer questions left
  than requested": the request size steps down (50 -> 25 -> 10 -> 5 -> 1). A
  refused request of size 1 marks the category exhausted.
* The API's reported total is used only as a hint for request size and for
  reporting. It is never trusted as proof that questions exist.
* Every loop is bounded: a per-category request cap, a cap on consecutive
  batches that add nothing new, a cap on token renewals, and bounded retries
  inside the client.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from . import OPENTDB_MAX_AMOUNT
from .api import (
    CODE_INVALID_PARAMETER,
    CODE_NO_RESULTS,
    CODE_SUCCESS,
    CODE_TOKEN_EMPTY,
    CODE_TOKEN_NOT_FOUND,
    OpenTDBClient,
    RetriesExhausted,
)
from .clean import raw_record_fingerprint
from .store import DataStore, utc_now

log = logging.getLogger(__name__)

SIZE_LADDER = (50, 25, 10, 5, 1)

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"
STATUS_INCOMPLETE = "incomplete"   # stopped by a safety cap; rerun may help
STATUS_FAILED = "failed"           # API rejected the category


class DownloadAborted(Exception):
    """Run stopped early (network failure); progress is saved and resumable."""


@dataclass
class DownloadConfig:
    categories: Optional[List[int]] = None
    batch_size: int = OPENTDB_MAX_AMOUNT
    max_stale_batches: int = 3
    max_requests_per_category: Optional[int] = None
    max_token_renewals: int = 3
    max_batches: Optional[int] = None  # total question requests this run (None = unlimited)
    update: bool = False

    def validate(self) -> None:
        if not 1 <= self.batch_size <= OPENTDB_MAX_AMOUNT:
            raise ValueError(f"batch_size must be 1..{OPENTDB_MAX_AMOUNT}")
        if self.max_stale_batches < 1:
            raise ValueError("max_stale_batches must be >= 1")
        if self.max_requests_per_category is not None and self.max_requests_per_category < 1:
            raise ValueError("max_requests_per_category must be >= 1")
        if self.max_batches is not None and self.max_batches < 1:
            raise ValueError("max_batches must be >= 1")


@dataclass
class RunSummary:
    started_at: str
    finished_at: Optional[str] = None
    outcome: str = "running"
    new_records: int = 0
    requests: int = 0
    categories: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    message: str = ""


def size_ladder(batch_size: int) -> List[int]:
    return [batch_size] + [s for s in SIZE_LADDER if s < batch_size]


def build_index(store: DataStore) -> Dict[int, Set[str]]:
    """Map category id -> set of record fingerprints found in raw batches."""
    index: Dict[int, Set[str]] = {}
    for _, doc in store.iter_raw_batches():
        params = (doc.get("request") or {}).get("params") or {}
        encoding = params.get("encode", "url3986")
        results = (doc.get("response") or {}).get("results") or []
        bucket = index.setdefault(int(doc.get("category_id")), set())
        for rec in results:
            bucket.add(raw_record_fingerprint(rec, encoding))
    return index


class Downloader:
    def __init__(self, client: OpenTDBClient, store: DataStore, config: Optional[DownloadConfig] = None):
        self.client = client
        self.store = store
        self.config = config or DownloadConfig()
        self.config.validate()
        self.state: Dict[str, Any] = {}
        self.index: Dict[int, Set[str]] = {}
        self.all_fingerprints: Set[str] = set()
        self.question_requests = 0
        self.summary = RunSummary(started_at=utc_now())

    # ------------------------------------------------------------ helpers
    def _cat_state(self, cat_id: int, name: str) -> Dict[str, Any]:
        cats = self.state.setdefault("categories", {})
        cs = cats.setdefault(str(cat_id), {})
        cs.setdefault("status", STATUS_PENDING)
        cs.setdefault("token_seen", 0)
        cs.setdefault("requests_total", 0)
        cs["name"] = name
        return cs

    def _save(self) -> None:
        self.store.save_state(self.state)

    def rebuild_index(self) -> None:
        """Rebuild the dedup index from raw batches (the source of truth)."""
        self.index = build_index(self.store)
        self.all_fingerprints = set().union(*self.index.values()) if self.index else set()

    def _obtain_token(self, reason: str) -> None:
        log.info("Requesting a new session token (%s)", reason)
        self.state["token"] = self.client.request_token()
        self.state["token_obtained_at"] = utc_now()
        for cs in self.state.get("categories", {}).values():
            cs["token_seen"] = 0
        self._save()

    def _budget_left(self) -> bool:
        return self.config.max_batches is None or self.question_requests < self.config.max_batches

    # --------------------------------------------------------------- run
    def run(self) -> RunSummary:
        self.store.init()
        self.state = self.store.load_state()
        self.rebuild_index()
        known_total = sum(len(v) for v in self.index.values())
        log.info("Output directory: %s (%d unique records already stored)", self.store.root, known_total)

        try:
            categories_payload = self.client.get_categories()
            snap = self.store.save_categories_snapshot(categories_payload)
            if snap:
                log.info("Saved category list snapshot: %s", snap)
            categories = sorted(
                ({"id": c["id"], "name": c["name"]} for c in categories_payload["trivia_categories"]),
                key=lambda c: c["id"],
            )
            available = {c["id"] for c in categories}
            if self.config.categories:
                unknown = sorted(set(self.config.categories) - available)
                if unknown:
                    raise ValueError(f"unknown category id(s): {unknown}; available: {sorted(available)}")
                categories = [c for c in categories if c["id"] in set(self.config.categories)]
            log.info("%d categor%s selected", len(categories), "y" if len(categories) == 1 else "ies")

            if self.config.update:
                self._prepare_update(categories)
            elif not self.state.get("token"):
                self._obtain_token("no saved token")
            else:
                log.info("Resuming with saved session token from %s", self.state.get("token_obtained_at"))

            for pos, cat in enumerate(categories, 1):
                cs = self._cat_state(cat["id"], cat["name"])
                if cs["status"] == STATUS_COMPLETE:
                    log.info("[%d/%d] %s: already complete (%d stored) — skipping",
                             pos, len(categories), cat["name"], len(self.index.get(cat["id"], ())))
                    self.summary.categories[cat["id"]] = {"name": cat["name"], "status": "skipped (complete)",
                                                          "new": 0}
                    continue
                if not self._budget_left():
                    log.info("Batch budget (--max-batches %d) used; stopping before %s",
                             self.config.max_batches, cat["name"])
                    self.summary.outcome = "partial"
                    break
                log.info("[%d/%d] %s (id %d)", pos, len(categories), cat["name"], cat["id"])
                self._download_category(cat["id"], cat["name"], cs)

            if self.summary.outcome == "running":
                statuses = [self.state["categories"][str(c["id"])]["status"] for c in categories]
                if any(s in (STATUS_FAILED, STATUS_INCOMPLETE) for s in statuses):
                    self.summary.outcome = "completed_with_problems"
                elif all(s == STATUS_COMPLETE for s in statuses):
                    self.summary.outcome = "complete"
                else:
                    self.summary.outcome = "partial"
        except RetriesExhausted as exc:
            self.summary.outcome = "aborted"
            self.summary.message = str(exc)
            log.error("Stopping: %s", exc)
            log.error("Progress has been saved. Re-run the same command to resume.")
            raise DownloadAborted(str(exc)) from exc
        except KeyboardInterrupt:
            self.summary.outcome = "interrupted"
            log.warning("Interrupted. Progress up to the last completed batch is saved; re-run to resume.")
            raise
        finally:
            self.summary.finished_at = utc_now()
            self.summary.requests = self.client.request_count
            self._record_run()
        return self.summary

    def _record_run(self) -> None:
        if not self.state:
            return
        runs = self.state.setdefault("runs", [])
        runs.append({
            "started_at": self.summary.started_at,
            "finished_at": self.summary.finished_at,
            "outcome": self.summary.outcome,
            "new_records": self.summary.new_records,
            "http_requests": self.summary.requests,
            "update": self.config.update,
            "message": self.summary.message,
        })
        del runs[:-50]
        try:
            self._save()
        except Exception:  # never mask the original error
            log.exception("could not save final state")

    def _prepare_update(self, categories: List[Dict[str, Any]]) -> None:
        token = self.state.get("token")
        if token:
            log.info("Update mode: resetting session token so all questions are offered again")
            self.state["token"] = self.client.reset_token(token)
            self.state["token_obtained_at"] = utc_now()
            for cs in self.state.get("categories", {}).values():
                cs["token_seen"] = 0
        else:
            self._obtain_token("update mode, no saved token")
        for cat in categories:
            cs = self._cat_state(cat["id"], cat["name"])
            cs["status"] = STATUS_PENDING
        self._save()

    # ----------------------------------------------------------- category
    def _download_category(self, cat_id: int, name: str, cs: Dict[str, Any]) -> None:
        cfg = self.config
        counts = self.client.get_category_count(cat_id)
        reported = int(counts.get("total_question_count", 0))
        cs["reported_total"] = reported
        cs["reported_counts"] = counts
        cs["status"] = STATUS_IN_PROGRESS
        cs["last_error"] = None
        self._save()

        known = self.index.setdefault(cat_id, set())
        start_known = len(known)
        ladder = size_ladder(cfg.batch_size)
        rung = 0
        stale = 0
        stale_limit = math.ceil(len(known) / cfg.batch_size) + cfg.max_stale_batches
        cap = cfg.max_requests_per_category or (
            math.ceil(max(reported, 1) / cfg.batch_size) * 2
            + math.ceil(len(known) / cfg.batch_size) + len(ladder) + 10
        )
        renewals = 0
        requests = 0
        reported_exceeded = False  # set once the API returns more than it reported

        def finish(status: str, reason: str) -> None:
            cs["status"] = status
            cs["finish_reason"] = reason
            cs["stored_unique"] = len(known)
            if status == STATUS_COMPLETE:
                cs["completed_at"] = utc_now()
            if status in (STATUS_FAILED, STATUS_INCOMPLETE):
                cs["last_error"] = reason
            self._save()
            added = len(known) - start_known
            self.summary.categories[cat_id] = {"name": name, "status": status, "new": added,
                                               "stored": len(known), "reported": reported, "reason": reason}
            shortfall = ""
            if status == STATUS_COMPLETE and len(known) < reported:
                shortfall = f" — {reported - len(known)} fewer than the API's reported total"
            log.info("    %s: %s (%s). +%d new, %d stored, API reports %d%s",
                     name, status, reason, added, len(known), reported, shortfall)

        while True:
            if requests >= cap:
                return finish(STATUS_INCOMPLETE, f"request cap of {cap} reached")
            if not self._budget_left():
                cs["status"] = STATUS_IN_PROGRESS
                self._save()
                self.summary.outcome = "partial"
                self.summary.categories[cat_id] = {"name": name, "status": "paused", "new": len(known) - start_known,
                                                   "stored": len(known), "reported": reported, "reason": "batch budget"}
                log.info("    Batch budget reached; %s will resume on the next run", name)
                return

            # Choose request size: never more than the API says this token has left.
            # If the token has already seen the reported total, probe with 1 to
            # confirm exhaustion (the reported total may be wrong either way).
            hint = reported - int(cs.get("token_seen", 0))
            amount = ladder[rung]
            if not reported_exceeded:
                if hint > 0:
                    amount = min(amount, hint)
                elif reported > 0:
                    amount = 1

            requests += 1
            cs["requests_total"] = int(cs.get("requests_total", 0)) + 1
            self.question_requests += 1
            batch = self.client.fetch_questions(cat_id, amount, self.state.get("token"))
            code = batch.response_code

            if code == CODE_SUCCESS:
                cs["token_seen"] = int(cs.get("token_seen", 0)) + len(batch.results)
                if not reported_exceeded and reported > 0 and cs["token_seen"] > reported:
                    # The reported total was too low; stop using it as a size hint
                    # and allow a bounded number of extra requests.
                    reported_exceeded = True
                    cap += math.ceil(reported / cfg.batch_size) + len(ladder) + 10
                    log.info("    API returned more questions than its reported total (%d); continuing", reported)
                encoding = batch.params.get("encode", "url3986")
                new_fps = []
                for rec in batch.results:
                    fp = raw_record_fingerprint(rec, encoding)
                    if fp not in known and fp not in new_fps:
                        new_fps.append(fp)
                if new_fps:
                    rel = self.store.write_raw_batch(cat_id, batch.params, batch.payload)
                    known.update(new_fps)
                    self.all_fingerprints.update(new_fps)
                    self.summary.new_records += len(new_fps)
                    stale = 0
                    dupes = len(batch.results) - len(new_fps)
                    pct = f"{100 * len(known) / reported:.0f}%" if reported else "n/a"
                    log.info("    batch %-3d +%d new%s -> %d/%d (%s) saved %s",
                             requests, len(new_fps), f" ({dupes} duplicate)" if dupes else "",
                             len(known), reported, pct, rel)
                else:
                    stale += 1
                    log.info("    batch %-3d %d records, all already stored (%d/%d stale batches)",
                             requests, len(batch.results), stale, stale_limit)
                self._save()
                if stale >= stale_limit:
                    return finish(STATUS_INCOMPLETE,
                                  f"{stale} consecutive batches returned no new questions")
                continue

            if code in (CODE_NO_RESULTS, CODE_TOKEN_EMPTY):
                self._save()
                if amount > 1:
                    # step down to the next rung below the amount we just tried
                    while rung < len(ladder) - 1 and ladder[rung] >= amount:
                        rung += 1
                    log.info("    %s for amount=%d; retrying with a smaller request",
                             batch.code_name, amount)
                    continue
                return finish(STATUS_COMPLETE, f"exhausted (API code {code}: {batch.code_name})")

            if code == CODE_TOKEN_NOT_FOUND:
                renewals += 1
                if renewals > cfg.max_token_renewals:
                    return finish(STATUS_FAILED, "session token repeatedly rejected (code 3)")
                self._obtain_token("token not found / expired (code 3)")
                rung = 0
                # a fresh token will re-offer stored questions; allow for that
                stale_limit = math.ceil(len(known) / cfg.batch_size) + cfg.max_stale_batches
                cap += math.ceil(len(known) / cfg.batch_size) + len(ladder)
                continue

            if code == CODE_INVALID_PARAMETER:
                return finish(STATUS_FAILED, "API rejected the request parameters (code 2)")

            return finish(STATUS_FAILED, f"unexpected API response code {code}")
