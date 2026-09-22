# Importing questions into PortaPak Quiz

This guide explains how to get the downloader's output into the PortaPak Quiz React/TypeScript
application.

> The PortaPak Quiz repository is private and was not inspected, and no changes were made to it
> while this guide was written. The file locations and code below are suggestions for a typical
> Vite or Create React App project. Adjust them to match how the app is actually structured.

## 1. Produce and check the export

```bash
python -m otdb_downloader download        # or: build, if the raw data is already downloaded
```

Before copying anything, open `data/reports/validation-report.md` and check:

- **Valid (exported)** is roughly what you expect (about 5,300 minus any rejections);
- every category's status is `complete`;
- the rejection reasons look sensible.

## 2. Files to copy

| From (downloader) | Suggested location (quiz app) | Purpose |
|---|---|---|
| `data/export/portapak-questions.json` | `public/data/portapak-questions.json` | Question data, loaded at runtime |
| `data/export/portapak-questions.d.ts` | `src/types/portapak-questions.d.ts` | TypeScript types |

**Loaded at runtime (`public/`)** is recommended. The file is roughly 2 MB for the full collection, stays out of the
JavaScript bundle, and can be replaced on the Pi without rebuilding the app.

**Imported at build time (`src/data/`)** also works (`import data from "./data/portapak-questions.json"`),
but you must rebuild whenever the questions change. The TypeScript project needs
`"resolveJsonModule": true`.

## 3. File format

```ts
interface PortaPakQuestionFile {
  format: "portapak-quiz-questions";   // check this
  schemaVersion: 1;                    // and this
  generatedAt: string;                 // ISO 8601, UTC
  generator: string;                   // e.g. "otdb-downloader 2.0.0"
  attribution: { source; url; licence; licenceUrl; notice };  // show this in the app
  categories: { id: number; name: string; questionCount: number }[];
  questions: {
    id: string;                        // stable, e.g. "otdb-502608b94dc57d31"
    categoryId: number;
    category: string;
    type: "multiple" | "boolean";
    difficulty: "easy" | "medium" | "hard";
    question: string;                  // plain UTF-8 text, no HTML entities, no URL encoding
    correctAnswer: string;
    incorrectAnswers: string[];        // 3 for "multiple", 1 for "boolean"; NOT shuffled
  }[];
}
```

The export contains only questions that passed validation. Every question has a complete answer
set, and no question text appears twice.

Things the app needs to do itself:

- **Shuffle the answers.** The correct answer is always stored separately and is never mixed in
  with the incorrect ones.
- **Render text as plain text** (the default in React: `{question.question}`). Do not use
  `dangerouslySetInnerHTML`. The text is already decoded.
- True/false questions use exactly `"True"` and `"False"`.

## 4. Example loader

```ts
// src/lib/questions.ts
import type { PortaPakQuestion, PortaPakQuestionFile } from "../types/portapak-questions";

export async function loadQuestions(url = "/data/portapak-questions.json"): Promise<PortaPakQuestionFile> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Could not load questions: HTTP ${res.status}`);
  const data = (await res.json()) as PortaPakQuestionFile;
  if (data.format !== "portapak-quiz-questions" || data.schemaVersion !== 1) {
    throw new Error(`Unsupported question file: ${data.format} v${data.schemaVersion}`);
  }
  return data;
}

/** Fisher–Yates shuffle (returns a new array). */
export function shuffle<T>(items: readonly T[]): T[] {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export function answersFor(q: PortaPakQuestion): string[] {
  // Keep True/False in a fixed order; shuffle multiple-choice answers.
  return q.type === "boolean" ? ["True", "False"] : shuffle([q.correctAnswer, ...q.incorrectAnswers]);
}

export function pickQuestions(
  all: PortaPakQuestion[],
  opts: { count: number; categoryId?: number; difficulty?: PortaPakQuestion["difficulty"]; exclude?: Set<string> },
): PortaPakQuestion[] {
  const pool = all.filter(
    (q) =>
      (opts.categoryId === undefined || q.categoryId === opts.categoryId) &&
      (opts.difficulty === undefined || q.difficulty === opts.difficulty) &&
      !opts.exclude?.has(q.id),
  );
  return shuffle(pool).slice(0, opts.count);
}
```

Because `id` is derived from the question content, it stays the same between builds. You can store
the IDs of questions already asked (for example in `localStorage`) and pass them as `exclude`.

## 5. Attribution in the app

CC BY-SA 4.0 requires visible attribution. The export's `attribution` field contains everything
needed:

```tsx
export function Credits({ attribution }: { attribution: PortaPakQuestionFile["attribution"] }) {
  return (
    <p>
      Trivia questions from <a href={attribution.url}>{attribution.source}</a>, licensed under{" "}
      <a href={attribution.licenceUrl}>{attribution.licence}</a>. Questions were decoded and
      reformatted, and invalid records were removed.
    </p>
  );
}
```

See [`../NOTICE.md`](../NOTICE.md) for the full licence notes.

## 6. Updating the questions later

```bash
python -m otdb_downloader download --update
```

Then copy the new `portapak-questions.json` over the old one. Existing questions keep their IDs.
New questions get new IDs. If OpenTDB edits a question, the edited version gets a new ID.

## 7. Checklist before deploying to the Pi

- [ ] `validation-report.md` reviewed; all categories `complete`
- [ ] `portapak-questions.json` copied into the app
- [ ] App checks `format` and `schemaVersion`
- [ ] Answers are shuffled; text is rendered as plain text
- [ ] OpenTDB attribution shown in the app
- [ ] App tested offline (no network needed once the file is bundled)
