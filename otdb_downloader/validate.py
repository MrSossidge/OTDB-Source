"""Validation rules for cleaned records.

Each issue has a code and a severity. ``error`` issues reject the record from
the PortaPak export; ``warning`` issues are reported but the record is kept.
Records are never repaired by inventing content.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional

from .clean import ENTITY_RE, CleanRecord, comparison_key

SUPPORTED_TYPES = {"multiple": 3, "boolean": 1}  # type -> required number of incorrect answers
SUPPORTED_DIFFICULTIES = ("easy", "medium", "hard")
BOOLEAN_VALUES = ("True", "False")

_PERCENT_RE = re.compile(r"%[0-9A-Fa-f]{2}")

ERROR = "error"
WARNING = "warning"

# code -> (severity, human description)
ISSUE_CODES: Dict[str, tuple] = {
    "malformed_record": (ERROR, "Record is not a JSON object or a field has the wrong type"),
    "missing_question": (ERROR, "Question text is missing"),
    "empty_question": (ERROR, "Question text is empty"),
    "missing_correct_answer": (ERROR, "Correct answer is missing"),
    "empty_correct_answer": (ERROR, "Correct answer is empty"),
    "missing_incorrect_answers": (ERROR, "Incorrect answers are missing"),
    "insufficient_incorrect_answers": (ERROR, "Fewer incorrect answers than the question type requires"),
    "unexpected_incorrect_answer_count": (WARNING, "More incorrect answers than the question type normally has"),
    "empty_incorrect_answer": (ERROR, "An incorrect answer is empty"),
    "duplicate_answers": (ERROR, "Two or more answers are identical (case/whitespace-insensitive)"),
    "invalid_boolean_answers": (ERROR, "True/false question does not have exactly True and False as answers"),
    "unsupported_type": (ERROR, "Question type is not 'multiple' or 'boolean'"),
    "invalid_difficulty": (ERROR, "Difficulty is not easy, medium or hard"),
    "invalid_category": (ERROR, "Category is missing or not in the OpenTDB category list"),
    "category_mismatch": (ERROR, "Record category does not match the category it was downloaded from"),
    "malformed_text": (ERROR, "Text contains undecodable bytes, control characters or double-escaped entities"),
    "suspicious_encoding": (WARNING, "Text contains sequences that look like leftover percent-encoding"),
    "duplicate_question": (ERROR, "Same question text as an earlier record"),
    "text_normalised": (WARNING, "Whitespace or HTML entities were normalised during cleaning"),
}


@dataclass
class Issue:
    code: str
    detail: str = ""

    @property
    def severity(self) -> str:
        return ISSUE_CODES[self.code][0]

    def to_dict(self) -> Dict[str, str]:
        d = {"code": self.code, "severity": self.severity}
        if self.detail:
            d["detail"] = self.detail
        return d


def _text_problems(value: str) -> List[str]:
    problems = []
    if "�" in value:
        problems.append("contains U+FFFD (undecodable bytes)")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        problems.append("contains control characters")
    if ENTITY_RE.search(value):
        problems.append("contains HTML entities after decoding (double-escaped)")
    return problems


def validate_record(rec: CleanRecord, category_names: Dict[int, str]) -> List[Issue]:
    """Validate a single record in isolation (duplicates are checked separately)."""
    issues: List[Issue] = []
    for s in rec.structural_issues:
        issues.append(Issue("malformed_record", s))
    if rec.structural_issues and rec.question is None and rec.type is None:
        return issues  # not a usable object at all

    # question
    if rec.question is None:
        issues.append(Issue("missing_question"))
    elif not rec.question:
        issues.append(Issue("empty_question"))

    # correct answer
    if rec.correct_answer is None:
        issues.append(Issue("missing_correct_answer"))
    elif not rec.correct_answer:
        issues.append(Issue("empty_correct_answer"))

    # type
    required: Optional[int] = None
    if rec.type not in SUPPORTED_TYPES:
        issues.append(Issue("unsupported_type", repr(rec.type)))
    else:
        required = SUPPORTED_TYPES[rec.type]

    # difficulty
    if rec.difficulty not in SUPPORTED_DIFFICULTIES:
        issues.append(Issue("invalid_difficulty", repr(rec.difficulty)))

    # incorrect answers
    incorrect = rec.incorrect_answers
    if incorrect is None:
        issues.append(Issue("missing_incorrect_answers"))
    else:
        present = [a for a in incorrect if a]
        if len(present) != len(incorrect):
            issues.append(Issue("empty_incorrect_answer"))
        if required is not None:
            if len(present) < required:
                issues.append(Issue("insufficient_incorrect_answers",
                                    f"{len(present)} of {required} required for '{rec.type}'"))
            elif len(present) > required:
                issues.append(Issue("unexpected_incorrect_answer_count",
                                    f"{len(present)} (expected {required})"))

    # duplicate answers within the record
    answers = [a for a in [rec.correct_answer] + list(incorrect or []) if a]
    keys = [comparison_key(a) for a in answers]
    if len(set(keys)) != len(keys):
        dupes = sorted({a for a, k in zip(answers, keys) if keys.count(k) > 1})
        issues.append(Issue("duplicate_answers", ", ".join(repr(d) for d in dupes)))

    # boolean rules
    if rec.type == "boolean" and rec.correct_answer and incorrect is not None:
        ok = (rec.correct_answer in BOOLEAN_VALUES and len(incorrect) == 1
              and incorrect[0] in BOOLEAN_VALUES and incorrect[0] != rec.correct_answer)
        if not ok:
            issues.append(Issue("invalid_boolean_answers",
                                f"correct={rec.correct_answer!r} incorrect={incorrect!r}"))

    # category mapping
    expected_name = category_names.get(rec.category_id) if rec.category_id is not None else None
    if not rec.category or rec.category not in category_names.values():
        issues.append(Issue("invalid_category", repr(rec.category)))
    elif expected_name is None:
        issues.append(Issue("invalid_category", f"downloaded under unknown category id {rec.category_id}"))
    elif rec.category != expected_name:
        issues.append(Issue("category_mismatch",
                            f"record says {rec.category!r}, downloaded from {expected_name!r} (id {rec.category_id})"))

    # malformed text
    fields = {"question": rec.question, "correct_answer": rec.correct_answer}
    for i, a in enumerate(incorrect or []):
        fields[f"incorrect_answers[{i}]"] = a
    for name, value in fields.items():
        if not value:
            continue
        for p in _text_problems(value):
            issues.append(Issue("malformed_text", f"{name}: {p}"))
        if _PERCENT_RE.search(value):
            issues.append(Issue("suspicious_encoding", f"{name}: {value!r}"))

    if rec.notes:
        issues.append(Issue("text_normalised", "; ".join(sorted(set(rec.notes)))))
    return issues


def is_valid(issues: List[Issue]) -> bool:
    return not any(i.severity == ERROR for i in issues)
