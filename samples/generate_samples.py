"""Regenerate the files in samples/ by running the real downloader against the
offline fake OpenTDB API used by the test suite.

    python samples/generate_samples.py

The output is NOT a live OpenTDB download. It contains:

* three real OpenTDB records (General Knowledge, CC BY-SA 4.0, opentdb.com),
  used to show genuine encoding and structure; and
* a handful of synthetic records written for this sample, including some that
  are deliberately flawed so the validation report shows rejections.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FakeClock, FakeOpenTDB  # noqa: E402
from otdb_downloader.api import ClientConfig, OpenTDBClient  # noqa: E402
from otdb_downloader.build import build  # noqa: E402
from otdb_downloader.downloader import DownloadConfig, Downloader  # noqa: E402
from otdb_downloader.store import DataStore  # noqa: E402

GK = "General Knowledge"
SCI = "Science: Computers"

# Real OpenTDB records (decoded), retrieved from opentdb.com api.php, category 9.
REAL = [
    {"type": "multiple", "difficulty": "medium", "category": GK,
     "question": "Which of the following carbonated soft drinks were introduced first?",
     "correct_answer": "Dr. Pepper", "incorrect_answers": ["Coca-Cola", "Sprite", "Mountain Dew"]},
    {"type": "multiple", "difficulty": "medium", "category": GK,
     "question": "What are the tallest twin buildings in the world, with a height of 1,483 ft (451.9 m)?",
     "correct_answer": "Petronas Twin Towers, Malaysia",
     "incorrect_answers": ["Emirates Towers, United Arab Emirates", "Huaguoyuan Towers, China", "Palm Towers, Qatar"]},
    {"type": "multiple", "difficulty": "easy", "category": GK,
     "question": "What is Cuba's official, most widely spoken language?",
     "correct_answer": "Spanish", "incorrect_answers": ["Portuguese", "French", "Italian"]},
]

# Synthetic records written for this sample.
SYNTHETIC = [
    {"type": "boolean", "difficulty": "easy", "category": GK,
     "question": "(Synthetic sample) Water boils at 100 °C at sea level.",
     "correct_answer": "True", "incorrect_answers": ["False"]},
    {"type": "multiple", "difficulty": "hard", "category": GK,
     "question": "(Synthetic sample, flawed) Which record is missing two incorrect answers?",
     "correct_answer": "This one", "incorrect_answers": ["Not this one"]},
    {"type": "multiple", "difficulty": "easy", "category": GK,
     "question": "(Synthetic sample, flawed) Which record repeats an answer?",
     "correct_answer": "Repeated", "incorrect_answers": ["repeated ", "Other", "Another"]},
    {"type": "multiple", "difficulty": "medium", "category": GK,
     "question": "What is Cuba's official, most widely spoken language?",
     "correct_answer": "Spanish (Cuban)", "incorrect_answers": ["English", "Creole", "Catalan"]},
    {"type": "multiple", "difficulty": "easy", "category": SCI,
     "question": "(Synthetic sample) What does &quot;CPU&quot; stand for?",
     "correct_answer": "Central Processing Unit",
     "incorrect_answers": ["Computer Personal Unit", "Central Process Utility", "Core Power Unit"]},
    {"type": "boolean", "difficulty": "medium", "category": SCI,
     "question": "(Synthetic sample, flawed) A true/false question with an invalid answer set.",
     "correct_answer": "Yes", "incorrect_answers": ["No"]},
]


def main() -> None:
    clock = FakeClock()
    cats = {
        9: {"name": GK, "questions": REAL + SYNTHETIC[:4]},
        18: {"name": SCI, "questions": SYNTHETIC[4:]},
    }
    api = FakeOpenTDB(clock, cats)
    tmp = Path(tempfile.mkdtemp())
    try:
        store = DataStore(tmp / "data")
        client = OpenTDBClient(ClientConfig(base_url="https://opentdb.test"), session=api,
                               clock=clock.monotonic, sleep=clock.sleep)
        Downloader(client, store, DownloadConfig()).run()
        build(store)
        out = ROOT / "samples" / "output"
        if out.exists():
            shutil.rmtree(out)
        for rel in ["raw/category_09/batch_00001.json", "clean/questions.json",
                    "export/portapak-questions.json", "export/portapak-questions.d.ts",
                    "reports/validation-report.json", "reports/validation-report.md", "state.json"]:
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(store.root / rel, dst)
        # keep the sample state free of the (fake) token
        st = json.loads((out / "state.json").read_text(encoding="utf-8"))
        st["token"] = "<redacted>"
        (out / "state.json").write_text(json.dumps(st, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Samples written to {out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
