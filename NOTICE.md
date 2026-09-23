# Licence and attribution notice

This repository contains two kinds of material under two different licences. Keep them separate.

## 1. Software (downloader code): GNU GPL v3.0

| Component | Copyright | Licence |
|---|---|---|
| `trivia.py` (original script) | © QuartzWarrior and contributors | GPL-3.0 |
| `otdb_downloader/`, `tests/`, `samples/generate_samples.py` | © MrSossidge and contributors. Derived from OTDB-Source by QuartzWarrior | GPL-3.0 |

- Upstream project: **OTDB-Source** by **QuartzWarrior**: <https://github.com/QuartzWarrior/OTDB-Source>
- This fork: <https://github.com/MrSossidge/OTDB-Source>
- Full licence text: [`LICENSE`](LICENSE) (GNU General Public License, version 3).

What the GPL-3.0 requires if you distribute this software, or a modified version of it:

- keep the existing copyright notices and the licence text;
- license the whole program, including your changes, under GPL-3.0;
- provide the corresponding source code, or a written offer to provide it;
- mark modified files as changed, with a relevant date. The `otdb_downloader` package is a new
  implementation that replaces the original script's approach. `trivia.py` itself is unchanged.

The GPL covers the **code**. It does not cover the trivia questions the code downloads.

## 2. Question content: CC BY-SA 4.0

Questions, answers and category names come from the **Open Trivia Database** (<https://opentdb.com/>),
which provides them under the **Creative Commons Attribution-ShareAlike 4.0 International** licence
(<https://creativecommons.org/licenses/by-sa/4.0/>).

This applies to:

- the legacy CSV files in the repository root;
- everything the downloader produces from API data: `raw/`, `clean/questions.json`,
  `export/portapak-questions.json`, the validation reports, and the files in `samples/output/`
  (apart from the synthetic sample records described in `samples/README.md`).

What CC BY-SA 4.0 requires when you share the questions, for example inside the PortaPak Quiz app:

1. **Attribution:** credit the Open Trivia Database and link to it.
2. **Licence notice:** name CC BY-SA 4.0 and link to the licence.
3. **Indicate changes:** say that the data was modified. The downloader decodes URL encoding and
   HTML entities, trims and collapses whitespace, and leaves out records that fail validation.
4. **ShareAlike:** share the question data, and any adapted version of it, under CC BY-SA 4.0
   (or a compatible licence).
5. **No additional restrictions:** do not add terms or technical measures that stop others from
   doing what the licence allows.

### Suggested attribution text

For the quiz app's About or Credits screen:

> Trivia questions from the [Open Trivia Database](https://opentdb.com/), used under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Questions were decoded and
> reformatted, and invalid records were removed.

Plain-text version:

> Trivia questions from the Open Trivia Database (https://opentdb.com/), licensed under
> CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/). Questions were decoded and
> reformatted, and invalid records were removed.

The same information is included in every export file, in its `attribution` field, so the app can
display it directly.

## 3. How the two licences combine in PortaPak Quiz

- The quiz **application code** has its own licence, chosen by its author. It only *uses* the
  question file, so the GPL on this downloader does not apply to the quiz app.
- The **question data** bundled with the app stays under CC BY-SA 4.0 and needs the attribution
  above.
- If PortaPak Quiz ever includes code copied from this repository, that code is covered by GPL-3.0.

*This notice summarises licence obligations. It is not legal advice.*
