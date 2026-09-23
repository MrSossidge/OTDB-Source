# Sample output

The files in `output/` show what each output of the downloader looks like.

**They are not the result of a live OpenTDB download.** They were produced by running the real
downloader and build code against the offline, simulated OpenTDB API used by the test suite:

```bash
python samples/generate_samples.py
```

The sample data contains:

| Records | Source |
|---|---|
| 3 General Knowledge questions (soft drinks, twin towers, Cuba's language) | Real OpenTDB records, CC BY-SA 4.0, opentdb.com |
| 6 records marked "(Synthetic sample…)" or duplicating a real question's text | Written for this sample. Four are deliberately flawed so the validation report shows rejections |

| File | Description |
|---|---|
| `output/raw/category_09/batch_00001.json` | One verbatim raw batch (URL-encoded, as returned by the API) |
| `output/clean/questions.json` | Every unique record, decoded, with its validation issues |
| `output/export/portapak-questions.json` | Valid records in PortaPak Quiz format |
| `output/export/portapak-questions.d.ts` | TypeScript types for the export |
| `output/reports/validation-report.md` / `.json` | Download and validation report |
| `output/state.json` | Progress file after a completed run (token redacted) |

For a real collection, run `python -m otdb_downloader download` (see the main README).
