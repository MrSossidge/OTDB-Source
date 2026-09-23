"""On-disk storage for downloaded data.

Layout (relative to the output directory)::

    otdb-store.json                     marker identifying a downloader data directory
    state.json                          progress/resume state (rewritten atomically)
    download.log                        append-only run log
    raw/categories/categories_*.json    verbatim category-list snapshots
    raw/category_09/batch_00001.json    verbatim API responses, one file per batch
    clean/questions.json                generated: every unique record + validation result
    export/portapak-questions.json      generated: valid records for PortaPak Quiz
    export/portapak-questions.d.ts      generated: TypeScript types for the export
    reports/validation-report.json|md   generated: download and validation report

Raw files are the source of truth. They are created with exclusive-create
semantics and are never modified or overwritten. Everything under ``clean/``,
``export/`` and ``reports/`` is derived and can be regenerated with
``python -m otdb_downloader build``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

MARKER_FILE = "otdb-store.json"
STATE_FILE = "state.json"
LOCK_FILE = ".download.lock"
STORE_SCHEMA = "otdb-downloader-store/1"
RAW_BATCH_SCHEMA = "otdb-raw-batch/1"

_BATCH_RE = re.compile(r"^batch_(\d{5,})\.json$")
_CATEGORY_DIR_RE = re.compile(r"^category_(\d+)$")


class StoreError(Exception):
    pass


class UnrecognisedOutputDir(StoreError):
    pass


class StoreLocked(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsync_dir(path: Path) -> None:
    # Directory fsync is not supported on Windows; best effort elsewhere.
    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON to ``path`` atomically (temp file + fsync + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def exclusive_write_json(path: Path, obj: Any) -> None:
    """Create ``path`` and write JSON; fail if it already exists.

    Data is written to a temporary file first and then hard-linked into place
    (falling back to an exclusive create where hard links are unavailable), so
    a crash can never leave a half-written raw file under the final name.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)  # atomic; raises FileExistsError if target exists
        except (AttributeError, NotImplementedError, PermissionError, OSError) as exc:
            if isinstance(exc, FileExistsError):
                raise
            # Filesystems without hard-link support: exclusive create + copy.
            with open(path, "x", encoding="utf-8", newline="\n") as out, \
                    open(tmp, "r", encoding="utf-8") as src:
                out.write(src.read())
                out.flush()
                os.fsync(out.fileno())
        _fsync_dir(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class DataStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ----------------------------------------------------------- paths
    @property
    def marker_path(self) -> Path:
        return self.root / MARKER_FILE

    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def log_path(self) -> Path:
        return self.root / "download.log"

    def category_dir(self, category_id: int) -> Path:
        return self.raw_dir / f"category_{category_id:02d}"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ------------------------------------------------------ lifecycle
    def is_initialised(self) -> bool:
        return self.marker_path.is_file()

    def init(self) -> None:
        """Prepare the output directory, refusing to adopt foreign content."""
        if self.root.exists() and not self.root.is_dir():
            raise UnrecognisedOutputDir(f"{self.root} exists and is not a directory")
        if self.is_initialised():
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
            if marker.get("schema") != STORE_SCHEMA:
                raise UnrecognisedOutputDir(
                    f"{self.marker_path} has unsupported schema {marker.get('schema')!r}"
                )
            return
        if self.root.exists() and any(self.root.iterdir()):
            raise UnrecognisedOutputDir(
                f"{self.root} is not empty and is not a downloader data directory. "
                "Choose an empty or new --output-dir so existing files are not overwritten."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        exclusive_write_json(self.marker_path, {"schema": STORE_SCHEMA, "created_at": utc_now()})

    def require_initialised(self) -> None:
        if not self.is_initialised():
            raise UnrecognisedOutputDir(
                f"{self.root} is not a downloader data directory (missing {MARKER_FILE})"
            )

    # ----------------------------------------------------------- lock
    def acquire_lock(self, break_lock: bool = False) -> None:
        path = self.root / LOCK_FILE
        if break_lock and path.exists():
            path.unlink()
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                info = path.read_text(encoding="utf-8").strip()
            except OSError:
                info = "unknown"
            raise StoreLocked(
                f"Another download appears to be running on {self.root} (lock: {info}). "
                "If you are sure no other run is active, re-run with --break-lock."
            )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"pid={os.getpid()} started={utc_now()}\n")

    def release_lock(self) -> None:
        try:
            (self.root / LOCK_FILE).unlink()
        except FileNotFoundError:
            pass

    # ---------------------------------------------------------- state
    def load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return new_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError(
                f"state file {self.state_path} is unreadable ({exc}). Raw batches are intact; "
                "move the state file aside and re-run to rebuild progress from raw data."
            ) from exc
        if state.get("schema") != 1:
            raise StoreError(f"unsupported state schema {state.get('schema')!r}")
        state.setdefault("categories", {})
        state.setdefault("runs", [])
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, state)

    # ------------------------------------------------------- raw data
    def next_batch_path(self, category_id: int) -> Path:
        d = self.category_dir(category_id)
        highest = 0
        if d.exists():
            for p in d.iterdir():
                m = _BATCH_RE.match(p.name)
                if m:
                    highest = max(highest, int(m.group(1)))
        return d / f"batch_{highest + 1:05d}.json"

    def write_raw_batch(self, category_id: int, request_params: Dict[str, Any],
                        payload: Dict[str, Any]) -> str:
        doc = {
            "schema": RAW_BATCH_SCHEMA,
            "fetched_at": utc_now(),
            "category_id": category_id,
            "request": {"endpoint": "api.php", "params": request_params},
            "response": payload,
        }
        for _ in range(5):
            path = self.next_batch_path(category_id)
            try:
                exclusive_write_json(path, doc)
                return self.rel(path)
            except FileExistsError:
                continue  # raced with something else; pick the next number
        raise StoreError(f"could not allocate a new batch file in {self.category_dir(category_id)}")

    def iter_raw_batches(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Yield (relative path, document) for every raw batch, in stable order."""
        if not self.raw_dir.exists():
            return
        cat_dirs = []
        for p in self.raw_dir.iterdir():
            m = _CATEGORY_DIR_RE.match(p.name)
            if p.is_dir() and m:
                cat_dirs.append((int(m.group(1)), p))
        for _, d in sorted(cat_dirs):
            batches = []
            for p in d.iterdir():
                m = _BATCH_RE.match(p.name)
                if m:
                    batches.append((int(m.group(1)), p))
            for _, p in sorted(batches):
                try:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise StoreError(f"raw batch {p} is unreadable: {exc}") from exc
                yield self.rel(p), doc

    def save_categories_snapshot(self, payload: Dict[str, Any]) -> Optional[str]:
        """Store the category list if it differs from the latest snapshot."""
        latest = self.latest_categories_snapshot()
        if latest is not None and latest[1].get("response") == payload:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        d = self.raw_dir / "categories"
        for n in range(1000):
            suffix = "" if n == 0 else f"_{n}"
            path = d / f"categories_{stamp}{suffix}.json"
            try:
                exclusive_write_json(path, {"schema": "otdb-raw-categories/1",
                                            "fetched_at": utc_now(), "response": payload})
                return self.rel(path)
            except FileExistsError:
                continue
        raise StoreError("could not allocate a category snapshot file")

    def latest_categories_snapshot(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        d = self.raw_dir / "categories"
        if not d.exists():
            return None
        files = sorted(p for p in d.iterdir() if p.name.startswith("categories_") and p.suffix == ".json")
        if not files:
            return None
        p = files[-1]
        return self.rel(p), json.loads(p.read_text(encoding="utf-8"))


def new_state() -> Dict[str, Any]:
    return {
        "schema": 1,
        "created_at": utc_now(),
        "updated_at": None,
        "token": None,
        "token_obtained_at": None,
        "categories": {},
        "runs": [],
    }
