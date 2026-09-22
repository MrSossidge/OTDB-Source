# OpenTDB download and validation report

Generated 2026-09-22T22:52:30Z by otdb-downloader 2.0.0.

## Totals

| Measure | Count |
|---|---:|
| Raw batches | 2 |
| Raw records downloaded | 9 |
| Exact duplicate records removed | 0 |
| Unique records | 9 |
| **Valid (exported)** | **5** |
| Rejected | 4 |
| Records with warnings (kept) | 2 |

## Rejection reasons

| Reason | Records | Description |
|---|---:|---|
| `insufficient_incorrect_answers` | 1 | Fewer incorrect answers than the question type requires |
| `duplicate_answers` | 1 | Two or more answers are identical (case/whitespace-insensitive) |
| `duplicate_question` | 1 | Same question text as an earlier record |
| `invalid_boolean_answers` | 1 | True/false question does not have exactly True and False as answers |

## Warnings (records kept)

| Warning | Records | Description |
|---|---:|---|
| `text_normalised` | 2 | Whitespace or HTML entities were normalised during cleaning |

## By category

| ID | Category | API reported | Downloaded | Valid | Rejected | Multiple | True/False | Easy | Medium | Hard | Status |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 9 | General Knowledge | 7 | 7 | 4 | 3 | 3 | 1 | 2 | 2 | 0 | complete |
| 18 | Science: Computers | 2 | 2 | 1 | 1 | 1 | 0 | 1 | 0 | 0 | complete |

## By difficulty

| Difficulty | Downloaded | Valid | Rejected |
|---|---:|---:|---:|
| easy | 4 | 3 | 1 |
| hard | 1 | 0 | 1 |
| medium | 4 | 2 | 2 |

## By question type

| Type | Downloaded | Valid | Rejected |
|---|---:|---:|---:|
| boolean (true/false) | 2 | 1 | 1 |
| multiple (multiple choice) | 7 | 4 | 3 |

## Rejected records

| ID | Category | Question | Issues |
|---|---:|---|---|
| `otdb-e313a859ce32534a` | 9 | (Synthetic sample, flawed) Which record is missing two incorrect answers? | insufficient_incorrect_answers (1 of 3 required for 'multiple') |
| `otdb-a743648d941343a1` | 9 | (Synthetic sample, flawed) Which record repeats an answer? | duplicate_answers ('Repeated', 'repeated') |
| `otdb-5a70af8d5585ee59` | 9 | What is Cuba's official, most widely spoken language? | duplicate_question (same text as otdb-bf2411669b0d74d5 (different correct answer)) |
| `otdb-b36e198acc6e8052` | 18 | (Synthetic sample, flawed) A true/false question with an invalid answer set. | invalid_boolean_answers (correct='Yes' incorrect=['No']) |
