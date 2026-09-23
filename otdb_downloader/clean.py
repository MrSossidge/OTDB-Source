"""Decoding and normalisation of raw OpenTDB records.

The downloader requests ``encode=url3986``: every string field (question,
answers, category, type, difficulty) is percent-encoded UTF-8. Some OpenTDB
source text also contains HTML entities, so after URL-decoding we decode
entities once. Nothing is invented: missing fields stay missing and are
reported by the validator.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, unquote_plus

ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_WS_RE = re.compile(r"\s+")

TEXT_FIELDS = ("category", "type", "difficulty", "question", "correct_answer")


def decode_value(value: str, encoding: str) -> str:
    """Decode a single API string according to the request's ``encode`` param."""
    if encoding == "url3986":
        text = unquote(value, encoding="utf-8", errors="replace")
    elif encoding == "urlLegacy":
        text = unquote_plus(value, encoding="utf-8", errors="replace")
    elif encoding == "base64":
        try:
            text = base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            text = "�"
    else:  # API default: HTML entities
        text = value
    return text


def normalise_text(text: str, notes: List[str]) -> str:
    if ENTITY_RE.search(text):
        text = html.unescape(text)
        notes.append("html_entities_decoded")
    collapsed = _WS_RE.sub(" ", text).strip()
    if collapsed != text:
        notes.append("whitespace_normalised")
    return collapsed


def comparison_key(text: str) -> str:
    """Key used to compare answers/questions for duplicates."""
    return _WS_RE.sub(" ", text).strip().casefold()


@dataclass
class CleanRecord:
    """A decoded record plus provenance. Fields are None when absent/invalid."""

    category_id: Optional[int]
    category: Optional[str]
    type: Optional[str]
    difficulty: Optional[str]
    question: Optional[str]
    correct_answer: Optional[str]
    incorrect_answers: Optional[List[Optional[str]]]
    raw_batch: str
    raw_index: int
    fetched_at: Optional[str]
    notes: List[str] = field(default_factory=list)
    structural_issues: List[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def id(self) -> str:
        return f"otdb-{self.fingerprint[:16]}"


def _decode_field(record: Dict[str, Any], name: str, encoding: str, notes: List[str],
                  issues: List[str]) -> Optional[str]:
    if name not in record or record[name] is None:
        return None
    value = record[name]
    if not isinstance(value, str):
        issues.append(f"field '{name}' is {type(value).__name__}, expected string")
        return None
    field_notes: List[str] = []
    text = normalise_text(decode_value(value, encoding), field_notes)
    notes.extend(f"{name}:{n}" for n in field_notes)
    return text


def clean_record(record: Any, *, category_id: Optional[int], encoding: str,
                 raw_batch: str, raw_index: int, fetched_at: Optional[str] = None) -> CleanRecord:
    notes: List[str] = []
    issues: List[str] = []
    if not isinstance(record, dict):
        issues.append(f"record is {type(record).__name__}, expected object")
        rec = CleanRecord(category_id, None, None, None, None, None, None,
                          raw_batch, raw_index, fetched_at, notes, issues)
        rec.fingerprint = _hash(json.dumps(record, sort_keys=True, default=str))
        return rec

    values = {name: _decode_field(record, name, encoding, notes, issues) for name in TEXT_FIELDS}

    incorrect: Optional[List[Optional[str]]] = None
    raw_incorrect = record.get("incorrect_answers")
    if raw_incorrect is not None:
        if isinstance(raw_incorrect, list):
            incorrect = []
            for i, item in enumerate(raw_incorrect):
                if isinstance(item, str):
                    item_notes: List[str] = []
                    incorrect.append(normalise_text(decode_value(item, encoding), item_notes))
                    notes.extend(f"incorrect_answers[{i}]:{n}" for n in item_notes)
                else:
                    issues.append(f"incorrect_answers[{i}] is {type(item).__name__}, expected string")
                    incorrect.append(None)
        else:
            issues.append(f"field 'incorrect_answers' is {type(raw_incorrect).__name__}, expected list")

    rec = CleanRecord(
        category_id=category_id,
        category=values["category"],
        type=values["type"],
        difficulty=values["difficulty"],
        question=values["question"],
        correct_answer=values["correct_answer"],
        incorrect_answers=incorrect,
        raw_batch=raw_batch,
        raw_index=raw_index,
        fetched_at=fetched_at,
        notes=notes,
        structural_issues=issues,
    )
    rec.fingerprint = record_fingerprint(rec)
    return rec


def record_fingerprint(rec: CleanRecord) -> str:
    """Content identity of a record: identical decoded content => same fingerprint."""
    incorrect = sorted(a or "" for a in (rec.incorrect_answers or []))
    key = [rec.category or "", rec.type or "", rec.difficulty or "",
           rec.question or "", rec.correct_answer or "", incorrect]
    return _hash(json.dumps(key, ensure_ascii=False))


def raw_record_fingerprint(record: Any, encoding: str = "url3986") -> str:
    """Fingerprint for a raw API record (used by the downloader for dedup)."""
    return clean_record(record, category_id=None, encoding=encoding,
                        raw_batch="", raw_index=0).fingerprint


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
