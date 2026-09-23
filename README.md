# OpenTDB Downloader — PortaPak Edition

A reliable, resumable command-line tool that downloads the complete question set from the
[Open Trivia Database](https://opentdb.com/) (OpenTDB), keeps every API response exactly as it was
received, and produces validated JSON ready to import into the **PortaPak Quiz** application
(an offline React/TypeScript quiz for the Raspberry Pi).

This is a fork of **[OTDB-Source](https://github.com/QuartzWarrior/OTDB-Source) by QuartzWarrior**.
The original `trivia.py` script and its CSV question files are kept in this repository unchanged.
See [Credits and licensing](#licence-and-attribution).

---

## Contents

- [Why this fork exists](#why-this-fork-exists)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation) — [Windows](#windows) · [Linux / Raspberry Pi OS](#linux--raspberry-pi-os)
- [Usage](#usage)
- [Command-line options](#command-line-options)
- [Output directory structure](#output-directory-structure)
- [Example JSON question](#example-json-question)
- [Resuming interrupted downloads](#resuming-interrupted-downloads)
- [Updating the question collection](#updating-the-question-collection)
- [Data validation](#data-validation)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)
- [Integration with PortaPak Quiz](#integration-with-portapak-quiz)
- [Legacy files](#legacy-files)
- [Licence and attribution](#licence-and-attribution)

---

## Why this fork exists

The original `trivia.py` saves only each question and its correct answer to CSV. A quiz application
also needs the incorrect answers, difficulty and question type, and it needs a download it can
trust. This fork adds:

- complete question records (all answers and metadata);
- the original API responses stored separately from cleaned, application-ready data;
- correct handling of every OpenTDB response code, session tokens, rate limiting and network failures;
- safe resumption after an interruption, with no duplicate questions;
- validation and a report of what was downloaded and what was rejected, and why.

## Features

What the downloader does now:

- **Finds categories dynamically** from `api_category.php`. Nothing is hard-coded.
- **Keeps complete records**: question, correct answer, *all* incorrect answers, category,
  difficulty and type.
- **Respects the rate limit.** OpenTDB allows one request per IP every 5 seconds. The client
  spaces every request (including token and count calls) at least 5 seconds apart and will not
  accept a lower `--min-interval`.
- **Handles response codes**:
  | Code | Meaning | What the downloader does |
  |---|---|---|
  | 0 | Success | Saves the batch |
  | 1 | No results (fewer questions left than requested) | Asks for fewer questions (50 → 25 → 10 → 5 → 1). If a request for 1 is refused, the category is complete |
  | 2 | Invalid parameter | Marks the category `failed` and moves on to the next one |
  | 3 | Token not found (expired) | Gets a new token and continues. Duplicates are filtered out. The number of renewals is limited |
  | 4 | Token empty | Same as code 1: asks for fewer questions, then marks the category complete |
  | 5 | Rate limit | Waits, then retries with exponential backoff |
- **Recovers from network failures.** Timeouts, connection errors, HTTP 429/5xx and malformed JSON
  are retried a limited number of times with exponential backoff (5 s, 10 s, 20 s …, capped). An
  HTTP 429 `Retry-After` header is respected.
- **Uses configurable timeouts** (connect and read) on every request.
- **Never loops forever.** Every category has a cap on requests, a cap on consecutive batches that
  return nothing new, and a cap on token renewals. The API's reported total is only used as a hint:
  the downloader handles categories that hold more or fewer questions than reported.
- **Saves progress after every batch.** Each batch with new questions is written to its own raw
  file, and then the progress file is saved atomically.
- **Resumes safely.** If you re-run after an interruption, it continues with the same session token.
  The duplicate index is rebuilt from the raw files, so a crash between writing a batch and saving
  progress cannot cause duplicates.
- **Never silently overwrites downloaded data.** Raw files are created with exclusive-create
  semantics and are never modified. The tool refuses to use a non-empty directory it did not create.
  A lock file prevents two downloads running on the same directory at once.
- **Removes duplicates** at two levels: identical records are stored once, and records that repeat
  the text of an earlier question are rejected by validation.
- **Decodes text correctly.** It requests RFC 3986 URL encoding (`encode=url3986`), decodes it as
  UTF-8, then decodes HTML entities once. Text that comes out double-escaped or cannot be decoded
  is flagged, not silently changed.
- **Validates** every record and writes JSON and Markdown reports.
- **Exports** a single JSON file plus TypeScript type definitions for PortaPak Quiz.
- **Reports progress** as it runs (batch-by-batch, per-category and run summaries) to the console
  and to `download.log`.

What it does **not** do: it does not shuffle answers (the quiz app should do that), it does not
enrich or rewrite questions, and it does not make up incorrect answers when they are missing.

## Requirements

- Python **3.8 or newer** (test suite run on Python 3.8, 3.10, 3.11, 3.12 and 3.13)
- [`requests`](https://pypi.org/project/requests/) 2.31 or newer (see `requirements.txt`)
- `pytest` 7.4 or newer, to run the tests (see `requirements-dev.txt`)
- Internet access to `https://opentdb.com`

A full download of the current collection (about 5,300 verified questions in 24 categories)
needs roughly 170–250 requests. At one request every 5 seconds, that takes about
**15–25 minutes**.

## Installation

> **Windows users:** read the [Windows note](#windows-cloning-the-repository) first. Some legacy CSV
> files have names that Windows cannot create.

### Windows

```powershell
# 1. Clone the fork (see the note below about the legacy CSV filenames)
git clone --no-checkout https://github.com/MrSossidge/OTDB-Source.git
cd OTDB-Source
git sparse-checkout set --no-cone "/*" "!/*.csv"
git checkout main

# 2. Create and activate a virtual environment
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
#   (Command Prompt: .venv\Scripts\activate.bat)
#   If PowerShell blocks the script: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 3. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

#### Windows: cloning the repository

Thirteen of the original CSV files have a colon in their names (for example
`Entertainment: Film.csv`). Windows filesystems cannot store a colon in a filename, so a normal
`git clone` stops with `error: invalid path`. The downloader does not need those files. You can:

- use the sparse-checkout commands above, which skip the root-level `*.csv` files; or
- clone inside **WSL** (Windows Subsystem for Linux), where the files check out normally.

Do **not** set `core.protectNTFS=false` to force the checkout. NTFS would treat the colon as an
alternate data stream separator and the files would not be what you expect.

### Linux / Raspberry Pi OS

```bash
# 1. Clone
git clone https://github.com/MrSossidge/OTDB-Source.git
cd OTDB-Source

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate
#   (Debian/Raspberry Pi OS may need: sudo apt install python3-venv)

# 3. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

To run the tests as well, install `requirements-dev.txt` instead of `requirements.txt`.

## Usage

All commands run from the repository root with the virtual environment active:

```bash
python -m otdb_downloader <command> [options]
```

| Command | What it does |
|---|---|
| `download` | Downloads questions, resuming automatically, then runs `build` |
| `build` | Regenerates the clean data, the PortaPak export and the reports from the raw files. Does not use the network |
| `status` | Shows progress for each category |
| `categories` | Lists the live OpenTDB categories and their IDs |

Examples:

```bash
# Download everything into ./data (about 15–25 minutes)
python -m otdb_downloader download

# Use a different directory
python -m otdb_downloader download --output-dir ~/trivia-data

# Trial run: only two categories, stop after 3 question requests
python -m otdb_downloader download -c 9,18 --max-batches 3

# See which categories are complete
python -m otdb_downloader status

# Rebuild the export and report after changing validation rules (no network)
python -m otdb_downloader build

# Collect questions added to OpenTDB since your last full download
python -m otdb_downloader download --update
```

Typical progress output:

```text
14:02:11 INFO    [1/24] General Knowledge (id 9)
14:02:21 INFO        batch 1   +50 new -> 50/469 (11%) saved raw/category_09/batch_00001.json
14:02:26 INFO        batch 2   +50 new -> 100/469 (21%) saved raw/category_09/batch_00002.json
...
14:03:06 INFO        General Knowledge: complete (exhausted (API code 4: token empty)). +469 new, 469 stored, API reports 469
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (every selected category complete, or the run stopped at `--max-batches` as asked) |
| 1 | Usage or configuration error (bad option, foreign output directory, lock held) |
| 2 | Stopped because of repeated network or API failures. Progress is saved; re-run to resume |
| 3 | Finished, but one or more categories are `failed` or `incomplete` (see `status`) |
| 130 | Interrupted with Ctrl+C. Progress is saved; re-run to resume |

## Command-line options

`download` (`categories` accepts the network options only; `build` and `status` accept the common options only):

| Option | Default | Description |
|---|---|---|
| `-o`, `--output-dir PATH` | `data` | Data directory. Must be empty, new, or one this tool created |
| `-c`, `--categories IDS` | all | Comma-separated category IDs, e.g. `9,18,23` |
| `--batch-size N` | `50` | Questions per request (1–50; 50 is the API maximum) |
| `--max-batches N` | unlimited | Stop after N question requests in this run (useful for trial runs) |
| `--update` | off | Reset the session token and scan every selected category again. Only new questions are stored |
| `--no-build` | off | Skip the build step after downloading |
| `--min-interval SECONDS` | `5.0` | Gap between API requests. Values below 5.0 are rejected |
| `--timeout SECONDS` | `30` | Read timeout per request |
| `--connect-timeout SECONDS` | `10` | Connect timeout per request |
| `--max-retries N` | `5` | Retries per request for network errors, HTTP 429/5xx, malformed JSON and API code 5 |
| `--backoff SECONDS` | `5` | First retry delay. Doubles on each retry |
| `--max-backoff SECONDS` | `120` | Longest single retry delay |
| `--max-stale-batches N` | `3` | Stop a category after N consecutive batches that add nothing new. An extra allowance is added for questions already stored, because a new token will offer them again |
| `--max-requests-per-category N` | automatic | Hard limit on requests per category. The automatic limit is based on the reported size |
| `--break-lock` | off | Remove a lock file left behind by a crashed run |
| `-v`, `--verbose` / `-q`, `--quiet` | — | More or less console output |

## Output directory structure

```text
data/
├── otdb-store.json                 # marks this as a downloader data directory
├── state.json                      # progress, session token, per-category status, run history
├── download.log                    # log of every run (appended)
├── raw/                            # SOURCE OF TRUTH: verbatim API responses, never modified
│   ├── categories/
│   │   └── categories_20260922T140211Z.json
│   ├── category_09/
│   │   ├── batch_00001.json
│   │   └── batch_00002.json
│   └── category_10/ ...
├── clean/
│   └── questions.json              # every unique record, decoded, with its validation issues
├── export/
│   ├── portapak-questions.json     # valid records only: import this into PortaPak Quiz
│   └── portapak-questions.d.ts     # TypeScript types for the export
└── reports/
    ├── validation-report.json
    └── validation-report.md
```

- `raw/` is only ever added to. Each batch file records when it was fetched, the request parameters
  (the session token is left out) and the full API response, still URL-encoded.
- `clean/`, `export/` and `reports/` are **generated**. Every `build` regenerates them from `raw/`,
  writing each file atomically. Edit the raw data, never these files.
- `/data/` is in `.gitignore`. Remove that line if you want to commit your collection.

The [`samples/output/`](samples/output) folder has an example of each file (see
[`samples/README.md`](samples/README.md)).

## Example JSON question

A raw record as OpenTDB returns it (inside `raw/category_09/batch_NNNNN.json` → `response.results`):

```json
{
  "type": "multiple",
  "difficulty": "medium",
  "category": "General%20Knowledge",
  "question": "Which%20of%20the%20following%20carbonated%20soft%20drinks%20were%20introduced%20first%3F",
  "correct_answer": "Dr.%20Pepper",
  "incorrect_answers": ["Coca-Cola", "Sprite", "Mountain%20Dew"]
}
```

The same record in `export/portapak-questions.json`:

```json
{
  "id": "otdb-502608b94dc57d31",
  "categoryId": 9,
  "category": "General Knowledge",
  "type": "multiple",
  "difficulty": "medium",
  "question": "Which of the following carbonated soft drinks were introduced first?",
  "correctAnswer": "Dr. Pepper",
  "incorrectAnswers": ["Coca-Cola", "Sprite", "Mountain Dew"]
}
```

*(Question content © Open Trivia Database contributors, CC BY-SA 4.0.)*

The `id` is made from a hash of the record's decoded content. The same question gets the same ID
in every build, so the quiz app can use it to track which questions have already been asked.

## Resuming interrupted downloads

Re-run the same command. That is all you need to do.

```bash
python -m otdb_downloader download
```

How resuming works:

1. Every batch that adds new questions is saved to a new raw file **before** progress is recorded.
2. On start-up the downloader reads every raw file to rebuild its list of stored questions, so it
   never relies on `state.json` alone.
3. It continues with the session token saved in `state.json`. OpenTDB remembers which questions
   that token has already served, so no questions are downloaded twice.
4. If more than 6 hours have passed, OpenTDB will have deleted the token (response code 3). The
   downloader gets a new one. The new token will offer questions again that you already have;
   they are recognised and not stored a second time.
5. Categories marked `complete` are skipped.

After Ctrl+C, a network failure (exit code 2) or a power cut, just run the command again. If a
crash left the lock file behind, the downloader will tell you. Add `--break-lock` once you are sure
no other download is running.

## Updating the question collection

OpenTDB adds and verifies questions over time. To collect new ones:

```bash
python -m otdb_downloader download --update
```

This resets the session token, marks every selected category as pending, and scans everything
again. Only questions you do not already have are stored, in new raw files. The build then
regenerates the export. It takes about as long as a first download.

To refresh just a few categories: `python -m otdb_downloader download --update -c 9,23`.

Questions that OpenTDB later removes or edits stay in your raw data. An edited question will
appear as a new record. If its text matches an existing question it is rejected as a
`duplicate_question`, and the earlier version is kept.

## Data validation

`build` checks every unique record. **Errors** exclude the record from the PortaPak export.
**Warnings** are reported, but the record is kept.

| Code | Severity | Check |
|---|---|---|
| `malformed_record` | error | Record is not a JSON object, or a field has the wrong type |
| `missing_question` / `empty_question` | error | No question text |
| `missing_correct_answer` / `empty_correct_answer` | error | No correct answer |
| `missing_incorrect_answers` | error | No incorrect answers |
| `insufficient_incorrect_answers` | error | Fewer than 3 (multiple choice) or 1 (true/false) |
| `empty_incorrect_answer` | error | An incorrect answer is blank |
| `duplicate_answers` | error | Two answers match (ignoring case and extra spaces) |
| `invalid_boolean_answers` | error | A true/false question whose answers are not exactly `True`/`False` |
| `unsupported_type` | error | Type is not `multiple` or `boolean` |
| `invalid_difficulty` | error | Difficulty is not `easy`, `medium` or `hard` |
| `invalid_category` | error | Category name is not in the OpenTDB category list |
| `category_mismatch` | error | The record's category differs from the category it was downloaded under |
| `malformed_text` | error | Undecodable bytes (U+FFFD), control characters, or HTML entities still present after decoding (double-escaped) |
| `duplicate_question` | error | Same question text (ignoring case and extra spaces) as an earlier valid record. The first one is kept |
| `unexpected_incorrect_answer_count` | warning | More incorrect answers than the type normally has |
| `suspicious_encoding` | warning | Contains something that looks like left-over percent-encoding (e.g. `%20`) |
| `text_normalised` | warning | Whitespace was trimmed or collapsed, or HTML entities were decoded |

Identical records (for example, ones offered again after a token renewal) are counted as
"exact duplicate records removed" and stored once.

The report (`reports/validation-report.md` and `.json`) contains:

- total raw records, duplicates removed, unique, valid and rejected counts;
- rejection and warning reasons with counts;
- a breakdown by category (API reported total, downloaded, valid, rejected, multiple-choice vs
  true/false, difficulty split, download status);
- breakdowns by difficulty and by question type;
- every rejected record with its reasons and the raw file it came from.

Missing incorrect answers are **never** made up. The record is flagged and left out of the export.
You can still see it, with its issues, in `clean/questions.json`.

## Running the tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests use a simulated OpenTDB API and a simulated clock, so they need no network access and
finish in about a second. They cover full downloads, interrupted and resumed downloads (including a
crash between writing a batch and saving progress), token expiry and token exhaustion, rate-limit
responses and request spacing, malformed responses, duplicate detection, loops that must stop,
the refusal to overwrite files, every validation rule, and the export format.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `error: invalid path 'Entertainment: Board Games.csv'` when cloning on Windows | Legacy filenames contain `:`. See [Windows: cloning the repository](#windows-cloning-the-repository) |
| `… is not empty and is not a downloader data directory` | The output directory already holds other files. The tool will not write into it. Choose a new or empty `--output-dir` |
| `Another download appears to be running … (lock: …)` | Another run is using the directory, or a crashed run left its lock behind. If you are sure nothing is running, add `--break-lock` |
| Exit code 2 and `failed after N attempt(s)` | Network or OpenTDB outage. Check `https://opentdb.com` in a browser, then re-run to resume. For a slow connection, increase `--timeout` and `--max-retries` |
| Many `rate limit` warnings | Another program on your network (or IP address) is also using OpenTDB. The downloader backs off automatically. You can raise `--min-interval` (e.g. `6`) |
| A category shows `incomplete` in `status` | It hit a safety limit (too many requests, or too many batches with nothing new). Re-run to continue. If it happens again, check `download.log` |
| A category shows `failed` | OpenTDB rejected the request (code 2) or kept rejecting the token. See `finish_reason` in `status`, and re-run later |
| Fewer questions than the API reported | OpenTDB's count can include questions it will not serve. The report shows both numbers. This is expected and not an error |
| `state file … is unreadable` | Rename `state.json` (e.g. to `state.json.bad`) and re-run. Progress is rebuilt from `raw/`. The token is lost, so already stored questions will be offered again and skipped |
| `ModuleNotFoundError: No module named 'requests'` | The virtual environment is not active, or dependencies are not installed. See [Installation](#installation) |
| `python` not found on Windows | Use `py -3` instead, or install Python from python.org with "Add to PATH" ticked |

## Integration with PortaPak Quiz

The file to import is **`data/export/portapak-questions.json`**, with types in
**`data/export/portapak-questions.d.ts`**. Step-by-step instructions, including suggested file
locations, a loader function and a shuffling example, are in
[`docs/PORTAPAK_IMPORT.md`](docs/PORTAPAK_IMPORT.md).

In short:

1. Run a download and check `reports/validation-report.md`.
2. Copy `portapak-questions.json` and `portapak-questions.d.ts` into the quiz project.
3. Load the JSON, check `format === "portapak-quiz-questions"` and `schemaVersion === 1`, then
   shuffle `correctAnswer` together with `incorrectAnswers` for each question.
4. Show the OpenTDB attribution (in the file's `attribution` field) somewhere in the app, such as an
   About screen. This is required by the CC BY-SA 4.0 licence.

## Legacy files

These files come from the upstream project and are kept exactly as they were:

- `trivia.py`: the original downloader (question + correct answer to CSV).
- `*.csv` in the repository root: 5,298 questions with their correct answers, still URL-encoded,
  one file per category. The count matches OpenTDB's verified total as of the last upstream update.

The new downloader does not read or write these files. A problem assessment of the original
script is in [`docs/ASSESSMENT.md`](docs/ASSESSMENT.md).

## Licence and attribution

This project has **two separate licences**: one for the code and one for the question data.

### Downloader code: GPL-3.0

- Original work: **OTDB-Source** © QuartzWarrior and contributors —
  <https://github.com/QuartzWarrior/OTDB-Source>.
- This fork and its changes: © MrSossidge and contributors.
- Licensed under the **GNU General Public License v3.0**. See [`LICENSE`](LICENSE). If you
  distribute this program or a modified version, you must provide the source under GPL-3.0 as well,
  keep the copyright and licence notices, and mark your changes.

### Question content: CC BY-SA 4.0

- Questions, answers and category names come from the **Open Trivia Database**
  (<https://opentdb.com/>) and are licensed under
  **[Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/)**.
- This applies to the legacy CSV files and to everything this tool downloads or produces from the
  data (raw, clean and export files).
- If you share an app or file that contains the questions, you must credit the Open Trivia
  Database, link to the licence, say that changes were made (text decoding and normalisation,
  removal of invalid records), and share the question data under CC BY-SA 4.0. The GPL does not
  apply to the question data, and CC BY-SA does not apply to the code.

Full details and ready-to-use attribution text are in [`NOTICE.md`](NOTICE.md).

*This is not legal advice. If you plan to distribute the app commercially, check the licence terms
yourself.*
