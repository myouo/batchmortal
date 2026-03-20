import argparse
import logging
import os
import queue
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

from api import build_paipu_urls, get_player_records, search_player
from browser import BrowserAutomator
from results import ResultWriter, parse_metadata

logging.basicConfig(level=logging.INFO, format="%(message)s")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch Mortal Analysis Script (Python/SeleniumBase Edition)"
    )
    parser.add_argument("nickname", help="Player nickname")
    parser.add_argument("--limit", type=int, default=10, help="Max records per mode (default: 10)")
    parser.add_argument("--modes", default="9", help="Comma-separated mode IDs (default: 9)")
    parser.add_argument("--model-tag", default="4.1b", help="Mortal network version (default: 4.1b)")
    parser.add_argument("--headless", action="store_true", help="Run browser headlessly")
    parser.add_argument("--dry-run", action="store_true", help="Only print URLs, skip browser")
    parser.add_argument(
        "--no-manual-verification",
        action="store_true",
        help="Legacy flag kept for compatibility",
    )
    parser.add_argument("--flare-url", help="Legacy flag kept for compatibility")
    parser.add_argument(
        "--save-screenshot",
        action="store_true",
        help="Save screenshot of the results (default: False)",
    )
    parser.add_argument(
        "--unsafe-parallel-review",
        action="store_true",
        help="Allow concurrent review submissions. Faster on paper, but often slower in practice due to Turnstile retries.",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "xlsx"],
        default="xlsx",
        help="Output format: csv or xlsx (default: xlsx)",
    )
    parser.add_argument(
        "--proxy",
        help="Proxy URL (e.g. http://127.0.0.1:7890). If omitted, attempts to use system proxy.",
    )
    parser.add_argument("--workers", type=int, default=3, help="Max parallel browser instances (default: 3)")
    return parser.parse_args()


def build_output_path(nickname: str, output_format: str) -> tuple[str, str]:
    safe_nick = "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )
    output_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", safe_nick)
    out_path = os.path.join(output_root, f"results.{output_format}")
    return output_root, out_path


def detect_proxy(explicit_proxy: str | None) -> str | None:
    if explicit_proxy:
        return explicit_proxy
    sys_proxies = urllib.request.getproxies()
    return sys_proxies.get("https") or sys_proxies.get("http")


def collect_tasks(account_id: int, modes: list[int], limit: int, output_root: str) -> list[dict]:
    tasks = []
    for mode in modes:
        print(f"\n[Mode {mode}] Fetching records...")
        try:
            records = get_player_records(account_id, limit, mode)
        except Exception as exc:
            logging.error(f"[ERROR] mode={mode}: {exc} - skipping this mode")
            continue

        if not records:
            logging.info(f"[mode={mode}] No records found. Skipping.")
            continue

        items = build_paipu_urls(records, account_id)
        mode_dir = os.path.join(output_root, f"mode_{mode}")
        for index, item in enumerate(items, start=1):
            tasks.append(
                {
                    "idx": index,
                    "total": len(items),
                    "mode": mode,
                    "uuid": item["uuid"],
                    "paipu_url": item["paipuUrl"],
                    "mode_dir": mode_dir,
                }
            )
    return tasks


def print_summary(args, modes):
    print("\n=== Batch Mortal Analysis ===")
    print(f"  Player:    {args.nickname}")
    print(f"  Modes:     {modes}")
    print(f"  Limit:     {args.limit} per mode")
    print(f"  ModelTag:  {args.model_tag}")
    print(f"  Headless:  {args.headless}")
    print(f"  DryRun:    {args.dry_run}")
    print("=============================\n")


def run_parallel_analysis(args, tasks: list[dict], out_path: str, automator: BrowserAutomator) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    requested_workers = min(args.workers, len(tasks))
    if args.unsafe_parallel_review:
        max_workers = requested_workers
    else:
        max_workers = min(requested_workers, 1)
        if requested_workers > max_workers:
            logging.info(
                "[Parallel] Review submissions are serialized by default because concurrent workers "
                "trigger long Turnstile waits and retries. Use --unsafe-parallel-review to override."
            )
    print(f"\n[Parallel] Starting analysis with {max_workers} persistent browsers")

    task_queue: queue.Queue = queue.Queue()
    result_queue: queue.Queue = queue.Queue()
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
        task_queue.put(task)

    def reporter():
        nonlocal total_processed, total_failed
        processed_count = 0
        expected_count = len(tasks)

        while processed_count < expected_count:
            result_event = result_queue.get()
            try:
                task = result_event["task"]
                timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                base_row = {
                    "nickname": args.nickname,
                    "mode": task["mode"],
                    "uuid": task["uuid"],
                    "paipuUrl": task["paipu_url"],
                    "timestamp": timestamp,
                }

                if result_event["status"] == "success":
                    result = result_event["result"]
                    parsed = parse_metadata(result["metadata"])
                    writer.write_row(
                        {
                            **base_row,
                            "resultUrl": result["resultUrl"],
                            "modelTag": parsed.get("modelTag") or args.model_tag,
                            "rating": parsed.get("rating", ""),
                            "aiConsistencyRate": parsed.get("aiConsistencyRate", ""),
                            "aiConsistencyNumerator": parsed.get("aiConsistencyNumerator", ""),
                            "aiConsistencyDenominator": parsed.get("aiConsistencyDenominator", ""),
                            "temperature": parsed.get("temperature", ""),
                            "gameLength": parsed.get("gameLength", ""),
                            "playerId": parsed.get("playerId", ""),
                            "reviewDuration": parsed.get("reviewDuration", ""),
                            "screenshotPath": result.get("screenshotPath", ""),
                        }
                    )
                    total_processed += 1
                    print(
                        "  OK "
                        f"rating={parsed.get('rating', 'N/A')} "
                        f"match={parsed.get('aiConsistencyRate', 'N/A')} "
                        f"({task['uuid']})"
                    )
                else:
                    writer.write_row(
                        {
                            **base_row,
                            "modelTag": args.model_tag,
                            "rating": "ERROR",
                        }
                    )
                    total_failed += 1
            finally:
                processed_count += 1
                result_queue.task_done()

    reporter_thread = threading.Thread(target=reporter, name="result-reporter")
    worker_threads = []

    try:
        reporter_thread.start()
        for index in range(max_workers):
            thread = threading.Thread(
                target=automator.run_worker,
                args=(task_queue, result_queue),
                name=f"browser-worker-{index + 1}",
            )
            thread.start()
            worker_threads.append(thread)

        task_queue.join()
        reporter_thread.join()
        for thread in worker_threads:
            thread.join()
    finally:
        writer.close()

    return total_processed, total_failed


def main():
    start_time = time.time()
    args = parse_args()
    modes = [int(mode.strip()) for mode in args.modes.split(",")]
    print_summary(args, modes)

    try:
        account_id = search_player(args.nickname)
    except Exception as exc:
        logging.error(f"[FATAL] {exc}")
        sys.exit(1)

    output_root, out_path = build_output_path(args.nickname, args.output)
    proxy = detect_proxy(args.proxy)

    if proxy:
        logging.info(f"[Proxy] Using proxy for browser: {proxy}")
    else:
        logging.info("[Proxy] No system proxy detected, running directly.")

    tasks = collect_tasks(account_id, modes, args.limit, output_root)
    total_processed = 0
    total_failed = 0

    if args.dry_run:
        for task in tasks:
            print(f"\n[{task['idx']}/{task['total']}] mode={task['mode']} uuid={task['uuid']}")
            print(f"  [dry-run] PaipuURL: {task['paipu_url']}")
            total_processed += 1
    elif tasks:
        automator = BrowserAutomator(headless=args.headless, proxy=proxy)
        total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator)

    elapsed = time.time() - start_time
    print("\n=== Done ===")
    print(f"  Succeeded: {total_processed}")
    print(f"  Failed:    {total_failed}")
    print(f"  Time:      {elapsed:.2f}s")
    if not args.dry_run:
        print(f"  Output:    {out_path}")
    print("============")


if __name__ == "__main__":
    main()
