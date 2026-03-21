import argparse
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from api import build_paipu_urls, get_player_records, search_player
from browser import BrowserAutomator, ReviewSubmissionCoordinator
from results import ResultWriter, parse_metadata, get_processed_uuids
from visualize import plot_results
from seleniumbase import SB
from config import load_config


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def log_line(message=""):
    logging.info(message)


def parse_args():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", help="Path to config file (yaml or toml)")
    pre_args, _ = pre_parser.parse_known_args()

    config = load_config(pre_args.config)

    parser = argparse.ArgumentParser(
        description="Batch Mortal Analysis Script (Python/SeleniumBase Edition)"
    )
    parser.add_argument("--config", help="Path to config file (yaml or toml)")
    parser.add_argument(
        "nickname", nargs="?", default=config.get("nickname"), help="Player nickname"
    )
    parser.add_argument(
        "--limit", type=int, default=config.get("limit", 10), help="Max records per mode (default: 10)"
    )
    parser.add_argument(
        "--modes", default=str(config.get("modes", "9")), help="Comma-separated mode IDs (default: 9)"
    )
    parser.add_argument(
        "--model-tag", default=config.get("model_tag", "4.1b"), help="Mortal network version (default: 4.1b)"
    )
    
    # For boolean options, we need to carefully handle true/false from config versus flag presence
    headless_default = config.get("headless", False)
    parser.add_argument(
        "--headless",
        action="store_true" if not headless_default else "store_false",
        default=headless_default,
        help="Run browser headlessly",
        dest="headless"
    )

    dry_run_default = config.get("dry_run", False)
    parser.add_argument(
        "--dry-run",
        action="store_true" if not dry_run_default else "store_false",
        default=dry_run_default,
        help="Only print URLs, skip browser",
        dest="dry_run"
    )

    no_manual_verification_default = config.get("no_manual_verification", False)
    parser.add_argument(
        "--no-manual-verification",
        action="store_true" if not no_manual_verification_default else "store_false",
        default=no_manual_verification_default,
        help="Legacy flag kept for compatibility",
    )
    parser.add_argument("--flare-url", default=config.get("flare_url"), help="Legacy flag kept for compatibility")

    save_screenshot_default = config.get("save_screenshot", False)
    parser.add_argument(
        "--save-screenshot",
        action="store_true" if not save_screenshot_default else "store_false",
        default=save_screenshot_default,
        help="Save screenshot of the results",
        dest="save_screenshot"
    )

    unsafe_parallel_default = config.get("unsafe_parallel_review", False)
    parser.add_argument(
        "--unsafe-parallel-review",
        action="store_true" if not unsafe_parallel_default else "store_false",
        default=unsafe_parallel_default,
        help="Allow concurrent review submissions.",
        dest="unsafe_parallel_review"
    )

    parser.add_argument(
        "--submit-interval",
        type=float,
        default=config.get("submit_interval", 6.0),
        help="Minimum spacing between controlled submissions in seconds.",
    )
    parser.add_argument(
        "--submit-cooldown",
        type=float,
        default=config.get("submit_cooldown", 30.0),
        help="Cooldown seconds after repeated review failures.",
    )

    prewarm_standby_default = config.get("prewarm_standby", False)
    parser.add_argument(
        "--prewarm-standby",
        action="store_true" if not prewarm_standby_default else "store_false",
        default=prewarm_standby_default,
        help="Experimental: use two persistent windows and alternate focus.",
        dest="prewarm_standby"
    )

    parser.add_argument(
        "--retry",
        type=int,
        default=config.get("retry", 3),
        help="Retry failed review items this many times.",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "xlsx"],
        default=config.get("output", "xlsx"),
        help="Output format: csv or xlsx",
    )
    parser.add_argument(
        "--proxy",
        default=config.get("proxy"),
        help="Proxy URL (e.g. http://127.0.0.1:7890).",
    )
    parser.add_argument(
        "--plot",
        choices=["none", "html", "png", "both"],
        default=config.get("plot", "none"),
        help="Generate a plot after analysis (none, html, png, both).",
    )
    
    args = parser.parse_args()
    
    if not args.nickname:
        parser.error("nickname is required either via positional argument or config file")
        
    return args


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


def collect_tasks(account_id: int, modes: list[int], limit: int, output_root: str, processed_uuids: set) -> list[dict]:
    tasks = []
    for mode in modes:
        log_line(f"[Mode {mode}] Fetching records...")
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
        for item in items:
            if item["uuid"] in processed_uuids:
                log_line(f"[Skip] uuid={item['uuid']} already processed.")
                continue
            tasks.append(
                {
                    "mode": mode,
                    "uuid": item["uuid"],
                    "paipu_url": item["paipuUrl"],
                    "start_time": item.get("startTime", ""),
                    "end_time": item.get("endTime", ""),
                    "mode_dir": mode_dir,
                }
            )

    total_tasks = len(tasks)
    for index, task in enumerate(tasks, start=1):
        task["idx"] = index
        task["total"] = total_tasks
        short_url = task["uuid"].split("-")[-1]
        task["log_prefix"] = f"[{index}/{total_tasks}][{short_url}]"

    return tasks


def print_summary(args, modes):
    log_line("=== Batch Mortal Analysis ===")
    log_line(f"  Player:    {args.nickname}")
    log_line(f"  Modes:     {modes}")
    log_line(f"  Limit:     {args.limit} per mode")
    log_line(f"  ModelTag:  {args.model_tag}")
    log_line(f"  Headless:  {args.headless}")
    log_line(f"  DryRun:    {args.dry_run}")
    log_line(f"  Retry:     {args.retry}")
    log_line("=============================")


def consume_result_event(args, writer: ResultWriter, result_event: dict) -> tuple[int, int]:
    task = result_event["task"]
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base_row = {
        "nickname": args.nickname,
        "mode": task["mode"],
        "uuid": task["uuid"],
        "paipuUrl": task["paipu_url"],
        "startTime": task.get("start_time", ""),
        "endTime": task.get("end_time", ""),
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
        log_line(
            f"{task['log_prefix']} OK "
            f"rating={parsed.get('rating', 'N/A')} "
            f"match={parsed.get('aiConsistencyRate', 'N/A')}"
        )
        return 1, 0

    writer.write_row(
        {
            **base_row,
            "modelTag": args.model_tag,
            "rating": "ERROR",
        }
    )
    log_line(f"{task['log_prefix']} ERROR")
    return 0, 1


def run_parallel_analysis(
    args,
    tasks: list[dict],
    out_path: str,
    automator: BrowserAutomator,
) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
    log_line("[Serial] Starting analysis with 1 persistent browser")

    try:
        with SB(uc=True, headless=automator.headless, proxy=automator.proxy) as sb:
            for task in tasks:
                result_event = None
                for attempt in range(args.retry + 1):
                    try:
                        result = automator.analyze_single(sb, task)
                        result_event = {"status": "success", "task": task, "result": result}
                        break
                    except Exception as exc:
                        prefix = task["log_prefix"]
                        logging.error(f"{prefix} ERROR exception: {exc}")
                        if attempt < args.retry:
                            logging.warning(
                                f"{prefix} RETRY ({attempt + 1}/{args.retry}) with a fresh page load."
                            )
                            continue

                        logging.error(
                            f"{prefix} SKIP permanently failed after {args.retry} retries."
                        )
                        result_event = {"status": "fail", "task": task}
                        break

                succeeded, failed = consume_result_event(args, writer, result_event)
                total_processed += succeeded
                total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def run_controlled_pipeline_analysis(args, tasks: list[dict], out_path: str, automator: BrowserAutomator) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot

    log_line("[Alternate] Starting two-window alternating review flow")

    try:
        for result_event in automator.iter_alternating_windows(tasks, max_retries=args.retry):
            succeeded, failed = consume_result_event(args, writer, result_event)
            total_processed += succeeded
            total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def main():
    configure_logging()
    start_time = time.time()
    args = parse_args()
    args.retry = max(0, args.retry)
    modes = [int(mode.strip()) for mode in args.modes.split(",")]
    print_summary(args, modes)

    try:
        account_id = search_player(args.nickname)
    except Exception as exc:
        logging.error(f"[FATAL] {exc}")
        sys.exit(1)

    output_root, out_path = build_output_path(args.nickname, args.output)
    processed_uuids = get_processed_uuids(out_path, args.output)
    proxy = detect_proxy(args.proxy)

    if proxy:
        logging.info(f"[Proxy] Using proxy for browser: {proxy}")
    else:
        logging.info("[Proxy] No system proxy detected, running directly.")

    tasks = collect_tasks(account_id, modes, args.limit, output_root, processed_uuids)
    total_processed = 0
    total_failed = 0

    if args.dry_run:
        for task in tasks:
            log_line(f"{task['log_prefix']} dry-run mode={task['mode']} paipu_url={task['paipu_url']}")
            total_processed += 1
    elif tasks:
        if args.unsafe_parallel_review:
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=None,
                controlled_submission=False,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator)
        elif args.prewarm_standby and len(tasks) >= 2:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=min(args.submit_interval, 1.0),
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_controlled_pipeline_analysis(args, tasks, out_path, automator)
        else:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=args.submit_interval,
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator)

    elapsed = time.time() - start_time
    log_line("=== Done ===")
    log_line(f"  Succeeded: {total_processed}")
    log_line(f"  Failed:    {total_failed}")
    log_line(f"  Time:      {elapsed:.2f}s")
    if not args.dry_run:
        log_line(f"  Output:    {out_path}")
        plot_results(args.nickname, args.plot, args.output)
    log_line("============")


if __name__ == "__main__":
    main()
