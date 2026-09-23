# Assessment of the original downloader (`trivia.py`)

This is the Phase 1 review, carried out before the `otdb_downloader` package was written.
Repository state reviewed: `MrSossidge/OTDB-Source` at `2968151`, identical to upstream
`QuartzWarrior/OTDB-Source` `main`.

## What was in the repository

- `trivia.py`: a single script of about 110 lines.
- `requirements.txt`: `requests` (no version pinned).
- 24 CSV files, one per category, 5,298 rows in total. Each row holds a URL-encoded question and
  its correct answer. The total matches OpenTDB's verified-question count reported by
  `api_count_global.php` on 2026-09-22.
- `LICENSE`: GNU GPL v3.0.
- A short README describing the script.

## How it worked

1. Requests one session token.
2. Loops over category IDs 9 to 32, which are hard-coded (it stops when `num == 33`).
3. On every iteration it fetches the full category list again, looks up the current category's
   name, `touch`es `<name>.csv`, and fetches the category's question count.
4. Counts the lines already in the CSV to work out how many questions are left, then requests up
   to 50 with `encode=url3986`.
5. Appends `question,correct_answer` (still URL-encoded) to the CSV.
6. Sleeps for 5.01 seconds.

## Problems found

| Area | Problem | Effect |
|---|---|---|
| Data | Throws away `incorrect_answers`, `difficulty` and `type` | The files cannot be used for a multiple-choice quiz |
| Data | Stores URL-encoded text | Every user of the files has to decode them |
| Response codes | Never reads `response_code`. Any response without `results` means "next category" | A rate-limit (5) or expired-token (3) response silently skips the rest of a category |
| Counting | `questions_written += count` adds the number *requested*, not the number received | Progress can be wrong, so the loop can move to the next category early |
| Categories | IDs 9 to 32 are hard-coded, and a list index `[num - 9]` is used to find the name | Breaks if categories are added, removed or reordered |
| Network | No timeouts, no retries, and `.json()` is called on any response | One slow or failed request crashes or hangs the run |
| Rate limit | 5.01 s sleep only after question requests. The token, category-list and count requests go out back-to-back | Breaks the one-request-per-5-seconds rule, which triggers code 5 |
| Resumption | Uses a new token each run but counts existing CSV lines to decide what's left | A rerun downloads questions it already has again and appends them, creating duplicates |
| Tokens | Does not handle token expiry (code 3) or token exhaustion (code 4), and never resets a token | See above |
| Overwrites/safety | Appends to CSVs with no record of what came from which request | Mistakes cannot be traced or undone |
| Duplicates | No duplicate detection | 3 repeated question texts exist in the current CSVs (Art, History, Science: Mathematics) |
| Portability | 13 CSV filenames contain `:` | `git clone` fails on Windows |

## The alternative JSON implementation

The brief mentioned a second, JSON-exporting implementation with weaknesses in error handling,
category iteration, resumption and data preservation. That script was not in this repository or
in the connected project folder, so it could not be reviewed directly. The good idea it was
described as having, keeping the complete API result, is included in this implementation's `raw/`
store.

## Licensing

- Code: GPL-3.0 (QuartzWarrior). The new package is a derivative work and stays GPL-3.0, with
  credit to the original.
- Question data: OpenTDB, CC BY-SA 4.0. This is licensed separately from the code. See `NOTICE.md`.

## How the new implementation addresses these problems

See the README's [Features](../README.md#features) section. In brief: categories are discovered
dynamically; complete records are stored verbatim; every response code has an explicit rule; all
requests are rate-limited, have timeouts and are retried a limited number of times; progress is
saved atomically after every batch; resuming reuses the same token and rebuilds the duplicate index
from the raw data; every loop has a limit; and validation and reports are generated from the raw
data.
