import json

import pytest

from otdb_downloader.build import build
from otdb_downloader.clean import clean_record, decode_value
from otdb_downloader.validate import is_valid, validate_record

from conftest import FakeOpenTDB, encode_record, make_categories, make_question

NAMES = {9: "General Knowledge", 18: "Science: Computers"}


def rec(**overrides):
    base = make_question("General Knowledge", 1)
    base.update(overrides)
    for k in [k for k, v in overrides.items() if v is DELETE]:
        del base[k]
    return base


DELETE = object()


def check(raw, category_id=9, encode=True):
    payload = encode_record(raw) if encode else raw
    r = clean_record(payload, category_id=category_id, encoding="url3986", raw_batch="x", raw_index=0)
    issues = validate_record(r, NAMES)
    return r, issues, {i.code for i in issues}


# ---------------------------------------------------------------- decoding
def test_url3986_unicode_decoding():
    assert decode_value("What%20nation%20produces%20roughly%2040%25%20of%20the%20world%E2%80%99s%20vanilla%3F",
                        "url3986") == "What nation produces roughly 40% of the world’s vanilla?"


def test_real_opentdb_record_decodes_cleanly():
    raw = {"type": "multiple", "difficulty": "medium", "category": "General%20Knowledge",
           "question": "Which%20of%20the%20following%20carbonated%20soft%20drinks%20were%20introduced%20first%3F",
           "correct_answer": "Dr.%20Pepper", "incorrect_answers": ["Coca-Cola", "Sprite", "Mountain%20Dew"]}
    r, issues, codes = check(raw, encode=False)
    assert r.question == "Which of the following carbonated soft drinks were introduced first?"
    assert r.category == "General Knowledge"
    assert r.incorrect_answers == ["Coca-Cola", "Sprite", "Mountain Dew"]
    assert is_valid(issues) and not codes


def test_html_entities_inside_url_encoding_are_decoded_once():
    r, issues, codes = check(rec(question="Who wrote &quot;Hamlet&quot;? &amp;amp; test"))
    assert r.question == 'Who wrote "Hamlet"? &amp; test'
    # the remaining &amp; means the source was double-escaped -> flagged, not silently changed
    assert "malformed_text" in codes


def test_plus_sign_is_not_treated_as_space():
    r, _, _ = check(rec(question="What is 2+2?"))
    assert r.question == "What is 2+2?"


def test_whitespace_is_normalised_and_warned():
    r, issues, codes = check(rec(correct_answer="Leonardo da Vinci "))
    assert r.correct_answer == "Leonardo da Vinci"
    assert codes == {"text_normalised"}
    assert is_valid(issues)


def test_invalid_utf8_is_malformed():
    raw = encode_record(rec())
    raw["question"] = "Bad%FFbyte%3F"
    _, issues, codes = check(raw, encode=False)
    assert "malformed_text" in codes and not is_valid(issues)


def test_leftover_percent_encoding_is_a_warning():
    raw = encode_record(rec())
    raw["question"] = "Double%2520encoded%3F"  # decodes to 'Double%20encoded?'
    _, issues, codes = check(raw, encode=False)
    assert "suspicious_encoding" in codes and is_valid(issues)


# -------------------------------------------------------------- validation
@pytest.mark.parametrize("overrides,code", [
    ({"question": DELETE}, "missing_question"),
    ({"question": "   "}, "empty_question"),
    ({"correct_answer": DELETE}, "missing_correct_answer"),
    ({"correct_answer": ""}, "empty_correct_answer"),
    ({"incorrect_answers": DELETE}, "missing_incorrect_answers"),
    ({"incorrect_answers": ["Only one", "Two"]}, "insufficient_incorrect_answers"),
    ({"incorrect_answers": ["A", "", "C"]}, "empty_incorrect_answer"),
    ({"incorrect_answers": ["Answer 1", "B", "C"]}, "duplicate_answers"),
    ({"incorrect_answers": ["b", "B ", "C"]}, "duplicate_answers"),
    ({"type": "free_text"}, "unsupported_type"),
    ({"difficulty": "extreme"}, "invalid_difficulty"),
    ({"category": "Not A Category"}, "invalid_category"),
    ({"category": "Science: Computers"}, "category_mismatch"),
    ({"type": "boolean", "correct_answer": "Yes", "incorrect_answers": ["No"]}, "invalid_boolean_answers"),
    ({"type": "boolean", "correct_answer": "True", "incorrect_answers": ["True"]}, "duplicate_answers"),
    ({"question": "Bell\u0007char?"}, "malformed_text"),
])
def test_validation_rules(overrides, code):
    _, issues, codes = check(rec(**overrides))
    assert code in codes
    assert not is_valid(issues)


def test_incorrect_answers_are_never_invented():
    r, issues, codes = check(rec(incorrect_answers=["Only one"]))
    assert r.incorrect_answers == ["Only one"]
    assert "insufficient_incorrect_answers" in codes


def test_valid_boolean_question():
    _, issues, codes = check(rec(type="boolean", correct_answer="False", incorrect_answers=["True"]))
    assert is_valid(issues) and not codes


def test_unknown_download_category_is_invalid():
    _, issues, codes = check(rec(), category_id=77)
    assert "invalid_category" in codes


def test_non_object_and_wrong_type_fields_are_malformed():
    r = clean_record(["not", "a", "dict"], category_id=9, encoding="url3986", raw_batch="x", raw_index=0)
    assert {i.code for i in validate_record(r, NAMES)} == {"malformed_record"}
    raw = encode_record(rec())
    raw["incorrect_answers"] = "A,B,C"
    _, issues, codes = check(raw, encode=False)
    assert "malformed_record" in codes and "missing_incorrect_answers" in codes


# ------------------------------------------------------------ build/export
def _download(clock, make_client, store, cats):
    from otdb_downloader.downloader import DownloadConfig, Downloader
    api = FakeOpenTDB(clock, cats)
    Downloader(make_client(api), store, DownloadConfig()).run()
    return build(store)


def test_duplicate_question_text_is_rejected_after_first(clock, make_client, store):
    cats = make_categories({9: 4}, names={9: "General Knowledge"})
    q = cats[9]["questions"]
    # same question text, different answer (as in the legacy History.csv)
    q.append(make_question("General Knowledge", 50, question=q[0]["question"]))
    # same question text differing only in case/whitespace
    q.append(make_question("General Knowledge", 51, question="  " + q[1]["question"].upper()))
    report = _download(clock, make_client, store, cats)
    assert report["totals"]["unique_records"] == 6
    assert report["rejection_reasons"] == {"duplicate_question": 2}
    export = json.loads((store.root / "export" / "portapak-questions.json").read_text())
    texts = [x["question"].casefold() for x in export["questions"]]
    assert len(texts) == len(set(texts)) == 4


def test_export_schema_and_report(clock, make_client, store):
    cats = make_categories({9: 10, 18: 5}, names=NAMES)
    bad = make_question("General Knowledge", 99)
    bad["incorrect_answers"] = ["Just one"]
    cats[9]["questions"].append(bad)
    report = _download(clock, make_client, store, cats)

    export = json.loads((store.root / "export" / "portapak-questions.json").read_text(encoding="utf-8"))
    assert export["format"] == "portapak-quiz-questions"
    assert export["schemaVersion"] == 1
    assert export["attribution"]["licence"] == "CC BY-SA 4.0"
    assert len(export["questions"]) == 15
    q = export["questions"][0]
    assert set(q) == {"id", "categoryId", "category", "type", "difficulty", "question",
                      "correctAnswer", "incorrectAnswers"}
    for q in export["questions"]:
        assert len(q["incorrectAnswers"]) == (3 if q["type"] == "multiple" else 1)
    assert len({q["id"] for q in export["questions"]}) == 15
    assert {c["id"]: c["questionCount"] for c in export["categories"]} == {9: 10, 18: 5}
    assert (store.root / "export" / "portapak-questions.d.ts").exists()

    t = report["totals"]
    assert (t["unique_records"], t["valid"], t["rejected"]) == (16, 15, 1)
    assert report["rejection_reasons"] == {"insufficient_incorrect_answers": 1}
    by_cat = {c["id"]: c for c in report["by_category"]}
    assert by_cat[9]["downloaded"] == 11 and by_cat[9]["valid"] == 10 and by_cat[9]["rejected"] == 1
    assert sum(v["valid"] for v in report["by_type"].values()) == 15
    assert report["by_type"]["boolean"]["valid"] == 3  # every 5th generated question
    assert sum(v.get("valid", 0) for v in report["by_difficulty"].values()) == 15
    md = (store.root / "reports" / "validation-report.md").read_text(encoding="utf-8")
    assert "insufficient_incorrect_answers" in md and "General Knowledge" in md

    clean = json.loads((store.root / "clean" / "questions.json").read_text(encoding="utf-8"))
    rejected = [r for r in clean["records"] if not r["valid"]]
    assert len(rejected) == 1 and rejected[0]["incorrect_answers"] == ["Just one"]


def test_ids_are_stable_across_builds(clock, make_client, store):
    _download(clock, make_client, store, make_categories({9: 5}, names=NAMES))
    first = json.loads((store.root / "export" / "portapak-questions.json").read_text())
    build(store)
    second = json.loads((store.root / "export" / "portapak-questions.json").read_text())
    assert [q["id"] for q in first["questions"]] == [q["id"] for q in second["questions"]]
