import argparse
import logging
import os
import sys
import time
import urllib.request

from batchmortal.api import (
    build_paipu_urls,
    get_player_records,
    get_player_nickname_by_id,
    parse_majsoul_paipu_url,
    search_player,
)
from batchmortal.browser import (
    BrowserAutomator,
    ReviewSubmissionCoordinator,
    normalize_review_language,
    normalize_review_ui,
)
from batchmortal.results import ResultWriter, parse_metadata, get_processed_uuids, read_result_rows
from batchmortal.timeutils import utc_now_string
from batchmortal.tenhou import (
    build_tenhou_paipu_urls,
    fetch_tenhou_player_records,
    normalize_tenhou_modes,
    parse_tenhou_log_id,
    parse_tenhou_log_url,
)
from batchmortal.visualize import plot_results
from seleniumbase import SB
from batchmortal.config import (
    load_config,
    normalize_source_mode,
    resolve_mode_config,
    source_for_mode,
)


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def log_line(message=""):
    logging.info(message)


def parse_review_language(value):
    try:
        return normalize_review_language(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_review_ui(value):
    try:
        return normalize_review_ui(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", help="Path to config file (yaml or toml)")
    pre_parser.add_argument("--mode")
    pre_parser.add_argument("--source", "--platform", dest="legacy_source")
    pre_args, _ = pre_parser.parse_known_args()

    raw_config = load_config(pre_args.config)
    requested_mode = pre_args.mode or pre_args.legacy_source
    try:
        config_mode, _, config = resolve_mode_config(
            raw_config,
            requested_mode=requested_mode,
        )
    except ValueError as exc:
        pre_parser.error(str(exc))

    parser = argparse.ArgumentParser(
        description="Batch Mortal Analysis Script (Python/SeleniumBase Edition)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # -- General Options --
    parser.add_argument("--config", help="Path to config file (yaml or toml)")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--mode",
        choices=["mj", "th", "0", "1"],
        default=config_mode,
        help="Exclusive source mode: mj/0 for Mahjong Soul, th/1 for Tenhou",
    )
    source_group.add_argument(
        "--source", "--platform",
        choices=["majsoul", "tenhou"],
        default=None,
        help="Legacy source selector; use --mode for new configurations",
        dest="legacy_source",
    )
    parser.add_argument(
        "--file",
        help="Load links directly from the specified file, one link per line"
    )
    dry_run_default = config.get("dry_run", False)
    parser.add_argument(
        "--dry-run", "--dry_run",
        action="store_true" if not dry_run_default else "store_false",
        default=dry_run_default,
        help="Only print URLs, skip browser",
        dest="dry_run"
    )

    # -- Target Options --
    target_group = parser.add_argument_group("Target Options")
    target_group.add_argument(
        "-p", "-u", "--player", dest="player", default=config.get("player") or config.get("nickname"), help="Player nickname on the selected source"
    )
    target_group.add_argument(
        "-a", "--account-id", "--account_id", dest="account_id", type=int, default=config.get("account_id"), help="Directly specify player account ID"
    )

    # -- Analysis Options --
    analysis_group = parser.add_argument_group("Analysis Options")
    analysis_group.add_argument(
        "--limit", type=int, default=config.get("limit", 10), help="Max records per mode"
    )
    analysis_group.add_argument(
        "--modes",
        default=None,
        help="Comma-separated modes: Mahjong Soul numeric IDs, or Tenhou all/4p/4p-east/4p-south/3p/3p-east/3p-south",
    )
    analysis_group.add_argument(
        "--model-tag", "--model_tag", default=config.get("model_tag", "4.1b"), help="Mortal network version", dest="model_tag"
    )
    review_language_config = config.get("review_language", config.get("lang"))
    try:
        review_language_default = normalize_review_language(review_language_config)
    except ValueError as exc:
        parser.error(str(exc))
    analysis_group.add_argument(
        "--review-language", "--review_language", "--lang",
        default=review_language_default,
        type=parse_review_language,
        metavar="{zh-CN,en,ja,ko}",
        help="Review page language; writes the mjai.ekyu.moe form field select[name='lang']",
        dest="review_language"
    )
    review_ui_config = config.get("review_ui", config.get("ui"))
    try:
        review_ui_default = normalize_review_ui(review_ui_config)
    except ValueError as exc:
        parser.error(str(exc))
    analysis_group.add_argument(
        "--review-ui", "--review_ui", "--ui",
        default=review_ui_default,
        type=parse_review_ui,
        metavar="{classic,killerducky}",
        help="Review result UI; KillerDucky metadata and bad-move data are parsed from report JSON",
        dest="review_ui",
    )
    analysis_group.add_argument(
        "--retry", type=int, default=config.get("retry", 3), help="Retry failed review items this many times"
    )
    analyze_bad_move_rate_default = bool(config.get("analyze_bad_move_rate", False))
    analysis_group.add_argument(
        "--badmove",
        action="store_true",
        default=analyze_bad_move_rate_default,
        help="Analyze bad move rate from the Mortal result page",
        dest="analyze_bad_move_rate"
    )

    # -- Browser / Network Options --
    browser_group = parser.add_argument_group("Browser & Network Options")
    headless_default = config.get("headless", False)
    browser_group.add_argument(
        "--headless",
        action="store_true" if not headless_default else "store_false",
        default=headless_default,
        help="Run browser headlessly",
        dest="headless"
    )
    browser_group.add_argument(
        "--proxy", default=config.get("proxy"), help="Proxy URL (e.g. http://127.0.0.1:7890)"
    )

    # -- Output Options --
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "--output", choices=["csv", "xlsx"], default=config.get("output", "xlsx"), help="Output format"
    )
    output_group.add_argument(
        "--plot", choices=["none", "html", "png", "both"], default=config.get("plot", "none"), help="Generate a plot after analysis"
    )
    output_group.add_argument(
        "--plot-limit", "--plot_limit", type=int, default=config.get("plot_limit"), help="Only use the latest N records for chart (default: all)", dest="plot_limit"
    )
    save_local_paipu_default = bool(config.get("save_local_paipu", False))
    output_group.add_argument(
        "--save-local",
        action="store_true",
        default=save_local_paipu_default,
        help="Save a local HTML copy of each Mortal result page",
        dest="save_local_paipu"
    )
    save_screenshot_default = config.get("save_screenshot", False)
    output_group.add_argument(
        "--save-screenshot", "--save_screenshot",
        action="store_true" if not save_screenshot_default else "store_false",
        default=save_screenshot_default,
        help="Save screenshot of the results",
        dest="save_screenshot"
    )

    # -- Advanced Submission Options --
    submit_group = parser.add_argument_group("Advanced Submission Options")
    unsafe_parallel_default = config.get("unsafe_parallel_review", False)
    submit_group.add_argument(
        "--unsafe-parallel-review", "--unsafe_parallel_review",
        action="store_true" if not unsafe_parallel_default else "store_false",
        default=unsafe_parallel_default,
        help="Allow concurrent review submissions",
        dest="unsafe_parallel_review"
    )
    submit_group.add_argument(
        "--submit-interval", "--submit_interval", type=float, default=config.get("submit_interval", 6.0), help="Minimum spacing between controlled submissions in seconds", dest="submit_interval"
    )
    submit_group.add_argument(
        "--submit-cooldown", "--submit_cooldown", type=float, default=config.get("submit_cooldown", 30.0), help="Cooldown seconds after repeated review failures", dest="submit_cooldown"
    )
    prewarm_standby_default = config.get("prewarm_standby", False)
    submit_group.add_argument(
        "--prewarm-standby", "--prewarm_standby",
        action="store_true" if not prewarm_standby_default else "store_false",
        default=prewarm_standby_default,
        help="Experimental: use two persistent windows and alternate focus",
        dest="prewarm_standby"
    )

    # -- Legacy Options --
    legacy_group = parser.add_argument_group("Legacy Options")
    no_manual_verification_default = config.get("no_manual_verification", False)
    legacy_group.add_argument(
        "--no-manual-verification", "--no_manual_verification",
        action="store_true" if not no_manual_verification_default else "store_false",
        default=no_manual_verification_default,
        help="Legacy flag kept for compatibility",
        dest="no_manual_verification"
    )
    legacy_group.add_argument(
        "--flare-url", "--flare_url", default=config.get("flare_url"), help="Legacy flag kept for compatibility", dest="flare_url"
    )
    
    args = parser.parse_args()

    if args.legacy_source:
        args.mode = normalize_source_mode(args.legacy_source)
    else:
        args.mode = normalize_source_mode(args.mode)
    args.source = source_for_mode(args.mode)

    if args.modes is None:
        fallback = "all" if args.source == "tenhou" else "9"
        args.modes = str(config.get("modes", fallback))

    if args.source == "tenhou":
        if not args.player:
            parser.error("Tenhou source requires -p/--player (a Tenhou player name)")
        if args.account_id:
            parser.error("--account-id is only supported by the majsoul source")
    elif not args.player and not args.account_id:
        parser.error("Mahjong Soul source requires -p/--player or -a/--account-id")
        
    args.target_name = args.player if args.player else str(args.account_id)
        
    return args


def build_output_path(nickname: str, output_format: str, source: str = "majsoul") -> tuple[str, str]:
    safe_nick = "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )
    results_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        source,
    )
    output_root = os.path.join(results_root, safe_nick)
    out_path = os.path.join(output_root, f"results.{output_format}")
    return output_root, out_path


def detect_proxy(explicit_proxy: str | None) -> str | None:
    if explicit_proxy:
        return explicit_proxy
    sys_proxies = urllib.request.getproxies()
    return sys_proxies.get("https") or sys_proxies.get("http")


def finalize_tasks(tasks: list[dict]) -> list[dict]:
    total_tasks = len(tasks)
    for index, task in enumerate(tasks, start=1):
        task["idx"] = index
        task["total"] = total_tasks
        short_url = task["uuid"].split("-")[-1]
        task["log_prefix"] = f"[{index}/{total_tasks}][{short_url}]"
    return tasks


def collect_majsoul_tasks(account_id: int, modes: list[int], limit: int, output_root: str, processed_uuids: set) -> list[dict]:
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
                    "source": "majsoul",
                    "mode": mode,
                    "uuid": item["uuid"],
                    "paipu_url": item["paipuUrl"],
                    "start_time": item.get("startTime", ""),
                    "end_time": item.get("endTime", ""),
                    "mode_dir": mode_dir,
                }
            )

    return finalize_tasks(tasks)


def collect_tenhou_tasks(
    records: list[dict],
    player_name: str,
    modes: tuple[str, ...],
    limit: int,
    output_root: str,
    processed_uuids: set,
) -> list[dict]:
    items = build_tenhou_paipu_urls(records, player_name, modes=modes, limit=limit)
    tasks = []
    for item in items:
        if item["uuid"] in processed_uuids:
            log_line(f"[Skip] uuid={item['uuid']} already processed.")
            continue
        mode = item["mode"]
        tasks.append(
            {
                "source": "tenhou",
                "mode": mode,
                "uuid": item["uuid"],
                "paipu_url": item["paipuUrl"],
                "start_time": item.get("startTime", ""),
                "end_time": item.get("endTime", ""),
                "mode_dir": os.path.join(output_root, f"mode_{mode}"),
            }
        )
    return finalize_tasks(tasks)


def collect_file_tasks(
    filename: str,
    source: str,
    output_root: str,
    processed_uuids: set,
) -> list[dict]:
    if source not in ("majsoul", "tenhou"):
        raise ValueError(f"Unsupported file source: {source}")

    tasks = []
    seen_uuids = set(processed_uuids)

    try:
        with open(filename, encoding="utf-8") as f:
            for line_number, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                tenhou_result = parse_tenhou_log_url(line)
                majsoul_result = parse_majsoul_paipu_url(line)

                if source == "tenhou":
                    if tenhou_result is None:
                        if majsoul_result is not None:
                            raise ValueError(
                                f"Line {line_number} is a Mahjong Soul link, but the selected "
                                "source is Tenhou. Use --mode mj or update the config."
                            )
                        logging.error(
                            f"[ERROR] line={line_number} is not a valid Tenhou paipu link, skip."
                        )
                        continue

                    log_id = tenhou_result[0]
                    if log_id in seen_uuids:
                        log_line(f"[Skip] uuid={log_id} already processed or duplicated.")
                        continue

                    metadata = parse_tenhou_log_id(log_id)
                    mode = metadata["mode"] if metadata else "file"
                    start_time = metadata["start_time"] if metadata else ""
                    seen_uuids.add(log_id)
                    tasks.append(
                        {
                            "source": "tenhou",
                            "mode": mode,
                            "uuid": log_id,
                            "paipu_url": line,
                            "start_time": start_time,
                            "end_time": "",
                            "mode_dir": os.path.join(output_root, f"mode_{mode}"),
                        }
                    )
                    continue

                if majsoul_result is None:
                    if tenhou_result is not None:
                        raise ValueError(
                            f"Line {line_number} is a Tenhou link, but the selected source is "
                            "Mahjong Soul. Use --mode th or update the config."
                        )
                    logging.error(
                        f"[ERROR] line={line_number} is not a valid Mahjong Soul paipu link, skip."
                    )
                    continue

                uuid, paipu_url = majsoul_result
                if uuid in seen_uuids:
                    log_line(f"[Skip] uuid={uuid} already processed or duplicated.")
                    continue

                seen_uuids.add(uuid)
                tasks.append(
                    {
                        "source": "majsoul",
                        "mode": "file",
                        "uuid": uuid,
                        "paipu_url": paipu_url,
                        "start_time": "",
                        "end_time": "",
                        "mode_dir": os.path.join(output_root, "mode_file"),
                    }
                )
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read paipu link file '{filename}': {exc}") from exc

    return finalize_tasks(tasks)


def print_summary(args, modes):
    log_line("=== Batch Mortal Analysis ===")
    target_display = args.target_name + (f" (ID: {args.account_id})" if args.account_id and args.target_name != str(args.account_id) else "")
    log_line(f"  Target:    {target_display}")
    log_line(f"  Mode:      {args.mode} ({args.source})")
    log_line(f"  Modes:     {modes}")
    log_line(f"  Limit:     {args.limit} per mode")
    log_line(f"  ModelTag:  {args.model_tag}")
    log_line(f"  Language:  {args.review_language}")
    log_line(f"  ReviewUI:  {args.review_ui}")
    log_line(f"  Headless:  {args.headless}")
    log_line(f"  DryRun:    {args.dry_run}")
    log_line(f"  Retry:     {args.retry}")
    log_line(f"  BadMove:   {args.analyze_bad_move_rate}")
    log_line(f"  SaveLocal: {args.save_local_paipu}")
    log_line("=============================")


def parse_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_analysis_stats() -> dict:
    return {
        "rating_sum": 0.0,
        "rating_count": 0,
        "ai_rate_sum": 0.0,
        "ai_rate_count": 0,
        "bad_move_5_sum": 0.0,
        "bad_move_5_count": 0,
        "bad_move_10_sum": 0.0,
        "bad_move_10_count": 0,
    }


def add_average_sample(stats: dict, prefix: str, value):
    parsed = parse_float(value)
    if parsed is None:
        return
    stats[f"{prefix}_sum"] += parsed
    stats[f"{prefix}_count"] += 1


def format_average(stats: dict, prefix: str, suffix: str = "") -> str:
    count = stats.get(f"{prefix}_count", 0)
    if not count:
        return "N/A"
    return f"{stats[f'{prefix}_sum'] / count:.3f}{suffix}"


def log_final_averages(args, stats: dict):
    log_line(f"  AvgRating: {format_average(stats, 'rating')}")
    log_line(f"  AvgMatch:  {format_average(stats, 'ai_rate', '%')}")
    if args.analyze_bad_move_rate:
        log_line(f"  AvgBadMove5:  {format_average(stats, 'bad_move_5', '%')}")
        log_line(f"  AvgBadMove10: {format_average(stats, 'bad_move_10', '%')}")


def add_result_row_to_stats(stats: dict, row: dict, include_bad_move: bool):
    if str(row.get("rating", "")).strip() == "ERROR":
        return

    add_average_sample(stats, "rating", row.get("rating", ""))
    add_average_sample(stats, "ai_rate", row.get("aiConsistencyRate", ""))
    if include_bad_move:
        add_average_sample(stats, "bad_move_5", row.get("badMoveRate5", ""))
        add_average_sample(stats, "bad_move_10", row.get("badMoveRate10", ""))


def create_analysis_stats_from_rows(rows: list[dict], include_bad_move: bool) -> dict:
    stats = create_analysis_stats()
    for row in rows:
        add_result_row_to_stats(stats, row, include_bad_move)
    return stats


def load_final_analysis_stats(out_path: str, output_format: str, include_bad_move: bool) -> dict | None:
    try:
        rows = read_result_rows(out_path, output_format)
    except Exception as exc:
        logging.warning(f"Failed to read final result stats from {out_path}: {exc}")
        return None

    return create_analysis_stats_from_rows(rows, include_bad_move)


def consume_result_event(args, writer: ResultWriter, result_event: dict, stats: dict | None = None) -> tuple[int, int]:
    task = result_event["task"]
    timestamp = utc_now_string()
    base_row = {
        "nickname": args.target_name,
        "source": task.get("source", args.source),
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
        bad_move_stats = result.get("badMoveStats") or {}
        rating_value = parse_float(parsed.get("rating", ""))
        if stats is not None:
            add_average_sample(stats, "rating", rating_value)
            add_average_sample(stats, "ai_rate", parsed.get("aiConsistencyRate", ""))
            if args.analyze_bad_move_rate:
                add_average_sample(stats, "bad_move_5", bad_move_stats.get("badMoveRate5", ""))
                add_average_sample(stats, "bad_move_10", bad_move_stats.get("badMoveRate10", ""))
        writer.write_row(
            {
                **base_row,
                "resultUrl": result["resultUrl"],
                "localPaipuPath": result.get("localPaipuPath", ""),
                "modelTag": parsed.get("modelTag") or args.model_tag,
                "rating": rating_value if rating_value is not None else "",
                "aiConsistencyRate": parsed.get("aiConsistencyRate", ""),
                "aiConsistencyNumerator": parsed.get("aiConsistencyNumerator", ""),
                "aiConsistencyDenominator": parsed.get("aiConsistencyDenominator", ""),
                "temperature": parsed.get("temperature", ""),
                "gameLength": parsed.get("gameLength", ""),
                "playerId": parsed.get("playerId", ""),
                "reviewDuration": parsed.get("reviewDuration", ""),
                "screenshotPath": result.get("screenshotPath", ""),
                "badMoveRate5": bad_move_stats.get("badMoveRate5", ""),
                "badMoveCount5": bad_move_stats.get("badMoveCount5", ""),
                "badMoveRate10": bad_move_stats.get("badMoveRate10", ""),
                "badMoveCount10": bad_move_stats.get("badMoveCount10", ""),
                "badMoveDenominator": bad_move_stats.get("badMoveDenominator", ""),
            }
        )
        message = (
            f"{task['log_prefix']} OK "
            f"rating={parsed.get('rating', 'N/A')} "
            f"match={parsed.get('aiConsistencyRate', 'N/A')}"
        )
        if args.analyze_bad_move_rate:
            bad_move_denominator = bad_move_stats.get("badMoveDenominator", "")
            bad_move_5 = (
                f"{bad_move_stats.get('badMoveCount5', '')}/{bad_move_denominator}="
                f"{bad_move_stats.get('badMoveRate5', '')}"
                if bad_move_denominator
                else "N/A"
            )
            bad_move_10 = (
                f"{bad_move_stats.get('badMoveCount10', '')}/{bad_move_denominator}="
                f"{bad_move_stats.get('badMoveRate10', '')}"
                if bad_move_denominator
                else "N/A"
            )
            message += f" badMove5={bad_move_5} badMove10={bad_move_10}"
        log_line(message)
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
    stats: dict | None = None,
) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
        task["save_local_paipu"] = args.save_local_paipu
        task["analyze_bad_move_rate"] = args.analyze_bad_move_rate
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

                succeeded, failed = consume_result_event(args, writer, result_event, stats)
                total_processed += succeeded
                total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def run_controlled_pipeline_analysis(
    args,
    tasks: list[dict],
    out_path: str,
    automator: BrowserAutomator,
    stats: dict | None = None,
) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
        task["save_local_paipu"] = args.save_local_paipu
        task["analyze_bad_move_rate"] = args.analyze_bad_move_rate

    log_line("[Alternate] Starting two-window alternating review flow")

    try:
        for result_event in automator.iter_alternating_windows(tasks, max_retries=args.retry):
            succeeded, failed = consume_result_event(args, writer, result_event, stats)
            total_processed += succeeded
            total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def ensure_uc_driver():
    import seleniumbase
    sb_dir = seleniumbase.__path__[0]
    drivers_dir = os.path.join(sb_dir, 'drivers')
    uc_name = 'uc_driver.exe' if os.name == 'nt' else 'uc_driver'
    if not os.path.exists(os.path.join(drivers_dir, uc_name)):
        logging.warning("uc_driver not found locally. Preparing to install via domestic mirror...")
        try:
            import install_uc_driver
            install_uc_driver.install_uc_driver()
        except ImportError:
            logging.error("install_uc_driver module not found. Please ensure install_uc_driver.py is in the project root.")
        except Exception as e:
            logging.error(f"Auto-installation of uc_driver failed: {e}")


def main():
    configure_logging()
    start_time = time.time()
    args = parse_args()
    args.retry = max(0, args.retry)
    try:
        if args.file:
            modes = ["from-file"]
        elif args.source == "tenhou":
            modes = normalize_tenhou_modes(args.modes)
        else:
            modes = [int(mode.strip()) for mode in args.modes.split(",") if mode.strip()]
            if not modes:
                raise ValueError("At least one Mahjong Soul mode is required.")
    except ValueError as exc:
        logging.error(f"[FATAL] {exc}")
        sys.exit(2)

    if not args.dry_run:
        ensure_uc_driver()

    account_id = None
    tenhou_records = None
    try:
        if args.file:
            args.target_name = args.player or str(args.account_id)
            args.limit = 0
        elif args.source == "tenhou":
            args.target_name, tenhou_records = fetch_tenhou_player_records(args.player)
        elif args.account_id:
            account_id = args.account_id
            if not args.player:
                # Attempt to fetch nickname to use as the target
                fetched_name = get_player_nickname_by_id(account_id)
                if fetched_name:
                    args.target_name = fetched_name
                    logging.info(f"[API] Fetched nickname: '{fetched_name}' for account_id={account_id}")
        else:
            account_id = search_player(args.player)
    except Exception as exc:
        logging.error(f"[FATAL] {exc}")
        sys.exit(1)

    print_summary(args, modes)

    output_root, out_path = build_output_path(args.target_name, args.output, args.source)
    processed_uuids = get_processed_uuids(out_path, args.output)
    proxy = detect_proxy(args.proxy)

    if proxy:
        logging.info(f"[Proxy] Using proxy for browser: {proxy}")
    else:
        logging.info("[Proxy] No system proxy detected, running directly.")

    if args.file:
        try:
            tasks = collect_file_tasks(args.file, args.source, output_root, processed_uuids)
        except ValueError as exc:
            logging.error(f"[FATAL] {exc}")
            sys.exit(1)
    elif args.source == "tenhou":
        tasks = collect_tenhou_tasks(
            tenhou_records,
            args.target_name,
            modes,
            args.limit,
            output_root,
            processed_uuids,
        )
    else:
        tasks = collect_majsoul_tasks(account_id, modes, args.limit, output_root, processed_uuids)
    total_processed = 0
    total_failed = 0
    analysis_stats = create_analysis_stats()

    if args.dry_run:
        for task in tasks:
            log_line(f"{task['log_prefix']} dry-run mode={task['mode']} paipu_url={task['paipu_url']}")
            total_processed += 1
    elif tasks:
        if args.unsafe_parallel_review:
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                review_language=args.review_language,
                review_ui=args.review_ui,
                submission_coordinator=None,
                controlled_submission=False,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator, analysis_stats)
        elif args.prewarm_standby and len(tasks) >= 2:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=min(args.submit_interval, 1.0),
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                review_language=args.review_language,
                review_ui=args.review_ui,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_controlled_pipeline_analysis(args, tasks, out_path, automator, analysis_stats)
        else:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=args.submit_interval,
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                review_language=args.review_language,
                review_ui=args.review_ui,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator, analysis_stats)

    elapsed = time.time() - start_time
    final_stats = analysis_stats
    if not args.dry_run:
        final_stats = load_final_analysis_stats(out_path, args.output, args.analyze_bad_move_rate) or analysis_stats

    log_line("=== Done ===")
    log_line(f"  Succeeded: {total_processed}")
    log_line(f"  Failed:    {total_failed}")
    log_final_averages(args, final_stats)
    log_line(f"  Time:      {elapsed:.2f}s")
    if not args.dry_run:
        log_line(f"  Output:    {out_path}")
        plot_results(
            args.target_name,
            args.plot,
            args.output,
            plot_limit=args.plot_limit,
            output_root=output_root,
        )
    log_line("============")


if __name__ == "__main__":
    main()
