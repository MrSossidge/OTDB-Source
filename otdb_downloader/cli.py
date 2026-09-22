"""Command-line interface: ``python -m otdb_downloader <command> [options]``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from . import OPENTDB_BASE_URL, OPENTDB_MAX_AMOUNT, OPENTDB_MIN_INTERVAL, __version__
from .api import ClientConfig, OpenTDBClient, OpenTDBError
from .build import build
from .downloader import DownloadAborted, DownloadConfig, Downloader, build_index
from .store import DataStore, StoreError

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ABORTED = 2
EXIT_PROBLEMS = 3
EXIT_INTERRUPTED = 130

log = logging.getLogger("otdb_downloader")


def _category_list(value: str) -> List[int]:
    try:
        ids = [int(v) for v in value.replace(" ", "").split(",") if v]
    except ValueError:
        raise argparse.ArgumentTypeError("categories must be comma-separated integers, e.g. 9,10,23")
    if not ids:
        raise argparse.ArgumentTypeError("no category ids given")
    return ids


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m otdb_downloader",
        description="Download, validate and export Open Trivia Database questions for PortaPak Quiz.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output-dir", type=Path, default=Path("data"),
                        help="data directory (default: ./data)")
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    common.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")

    net = argparse.ArgumentParser(add_help=False)
    net.add_argument("--base-url", default=OPENTDB_BASE_URL, help=argparse.SUPPRESS)
    net.add_argument("--min-interval", type=float, default=OPENTDB_MIN_INTERVAL,
                     help=f"seconds between API requests, minimum {OPENTDB_MIN_INTERVAL} (default: %(default)s)")
    net.add_argument("--timeout", type=float, default=30.0,
                     help="read timeout per request in seconds (default: %(default)s)")
    net.add_argument("--connect-timeout", type=float, default=10.0,
                     help="connect timeout in seconds (default: %(default)s)")
    net.add_argument("--max-retries", type=int, default=5,
                     help="retries per request for network errors, HTTP 429/5xx, malformed JSON "
                          "and API rate-limit responses (default: %(default)s)")
    net.add_argument("--backoff", type=float, default=5.0,
                     help="initial retry backoff in seconds, doubled each retry (default: %(default)s)")
    net.add_argument("--max-backoff", type=float, default=120.0,
                     help="upper bound for a single backoff delay (default: %(default)s)")

    d = sub.add_parser("download", parents=[common, net],
                       help="download questions (resumes automatically), then build outputs")
    d.add_argument("-c", "--categories", type=_category_list,
                   help="only these category ids, comma-separated (default: all)")
    d.add_argument("--batch-size", type=int, default=OPENTDB_MAX_AMOUNT,
                   help=f"questions per request, 1-{OPENTDB_MAX_AMOUNT} (default: %(default)s)")
    d.add_argument("--max-batches", type=int,
                   help="stop after this many question requests (useful for trial runs)")
    d.add_argument("--max-stale-batches", type=int, default=3,
                   help="stop a category after this many consecutive batches with nothing new, "
                        "plus an allowance for already-stored questions (default: %(default)s)")
    d.add_argument("--max-requests-per-category", type=int,
                   help="hard cap on requests per category (default: derived from reported size)")
    d.add_argument("--update", action="store_true",
                   help="reset the session token and re-scan categories to collect newly added questions")
    d.add_argument("--no-build", action="store_true", help="skip building clean/export/report files")
    d.add_argument("--break-lock", action="store_true",
                   help="remove a stale lock left by a crashed run")

    sub.add_parser("build", parents=[common],
                   help="rebuild clean data, PortaPak export and validation report from raw batches")
    sub.add_parser("status", parents=[common], help="show download progress per category")
    sub.add_parser("categories", parents=[common, net], help="list live OpenTDB categories")
    return p


def _setup_logging(args: argparse.Namespace, store: Optional[DataStore] = None) -> None:
    level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if store is not None and store.root.is_dir():
        fh = logging.FileHandler(store.log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        root.addHandler(fh)


def _client_config(args: argparse.Namespace) -> ClientConfig:
    cfg = ClientConfig(base_url=args.base_url, min_interval=args.min_interval,
                       connect_timeout=args.connect_timeout, read_timeout=args.timeout,
                       max_retries=args.max_retries, backoff_base=args.backoff,
                       backoff_max=args.max_backoff)
    cfg.validate()
    return cfg


def _client(args: argparse.Namespace) -> OpenTDBClient:
    return OpenTDBClient(_client_config(args))


def cmd_download(args: argparse.Namespace) -> int:
    store = DataStore(args.output_dir)
    try:
        _client_config(args)  # validate network options before touching the disk
        client = _client(args)
        config = DownloadConfig(categories=args.categories, batch_size=args.batch_size,
                                max_batches=args.max_batches, max_stale_batches=args.max_stale_batches,
                                max_requests_per_category=args.max_requests_per_category,
                                update=args.update)
        config.validate()
        store.init()
    except (ValueError, StoreError) as exc:
        _setup_logging(args)
        log.error("%s", exc)
        return EXIT_USAGE
    _setup_logging(args, store)
    try:
        store.acquire_lock(break_lock=args.break_lock)
    except StoreError as exc:
        log.error("%s", exc)
        return EXIT_USAGE

    code = EXIT_OK
    try:
        summary = Downloader(client, store, config).run()
        log.info("Run finished: %s — %d new records, %d HTTP requests",
                 summary.outcome, summary.new_records, summary.requests)
        if summary.outcome == "completed_with_problems":
            code = EXIT_PROBLEMS
    except DownloadAborted:
        code = EXIT_ABORTED
    except KeyboardInterrupt:
        code = EXIT_INTERRUPTED
    except ValueError as exc:
        log.error("%s", exc)
        code = EXIT_USAGE
    except (OpenTDBError, StoreError) as exc:
        log.error("%s", exc)
        code = EXIT_ABORTED
    finally:
        store.release_lock()

    if not args.no_build and code != EXIT_USAGE:
        try:
            report = build(store)
            _print_totals(report)
        except StoreError as exc:
            log.error("build failed: %s", exc)
            code = code or EXIT_PROBLEMS
    if code == EXIT_ABORTED or code == EXIT_INTERRUPTED:
        log.info("Re-run the same command to resume from the last saved batch.")
    return code


def _print_totals(report) -> None:
    t = report["totals"]
    log.info("Totals: %d unique, %d valid, %d rejected (%d exact duplicates removed)",
             t["unique_records"], t["valid"], t["rejected"], t["duplicate_records_removed"])
    log.info("Outputs: clean/questions.json, export/portapak-questions.json, reports/validation-report.md")


def cmd_build(args: argparse.Namespace) -> int:
    store = DataStore(args.output_dir)
    _setup_logging(args, store if store.is_initialised() else None)
    try:
        report = build(store)
    except StoreError as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    _print_totals(report)
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    store = DataStore(args.output_dir)
    _setup_logging(args)
    try:
        store.require_initialised()
        state = store.load_state()
    except StoreError as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    index = build_index(store)
    print(f"Data directory: {store.root}")
    print(f"Token obtained: {state.get('token_obtained_at') or 'never'}")
    print(f"{'ID':>3}  {'Category':<40} {'Stored':>6} {'API':>6}  Status")
    total = 0
    for cid, cs in sorted(state.get("categories", {}).items(), key=lambda kv: int(kv[0])):
        stored = len(index.get(int(cid), ()))
        total += stored
        rep = cs.get("reported_total")
        print(f"{cid:>3}  {cs.get('name', '')[:40]:<40} {stored:>6} {rep if rep is not None else '-':>6}  "
              f"{cs.get('status')}" + (f" ({cs.get('finish_reason')})" if cs.get("finish_reason") else ""))
    print(f"Total unique records stored: {total}")
    runs = state.get("runs", [])
    if runs:
        r = runs[-1]
        print(f"Last run: {r.get('started_at')} -> {r.get('outcome')} (+{r.get('new_records')} new)")
    return EXIT_OK


def cmd_categories(args: argparse.Namespace) -> int:
    _setup_logging(args)
    try:
        data = _client(args).get_categories()
    except (OpenTDBError, ValueError) as exc:
        log.error("%s", exc)
        return EXIT_ABORTED
    for c in sorted(data["trivia_categories"], key=lambda c: c["id"]):
        print(f"{c['id']:>3}  {c['name']}")
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"download": cmd_download, "build": cmd_build, "status": cmd_status,
                "categories": cmd_categories}
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
