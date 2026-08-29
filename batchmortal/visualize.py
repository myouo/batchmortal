import csv
import html
import json
import logging
import math
import os
import urllib.parse
from datetime import datetime
from statistics import median
from string import Template

import openpyxl


def _safe_nickname(nickname: str) -> str:
    return "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )


def _parse_time(value) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return 0.0


def _record_sort_time(record: dict) -> float:
    start_time = _parse_time(record.get("startTime"))
    if start_time:
        return start_time
    return _parse_time(record.get("timestamp"))


def read_results(
    nickname: str,
    output_format: str = "xlsx",
    output_root: str | None = None,
) -> list[dict]:
    safe_nick = _safe_nickname(nickname)
    if output_root is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_root = os.path.join(base_dir, "results", "majsoul", safe_nick)
    filepath = os.path.join(output_root, f"results.{output_format}")

    if not os.path.exists(filepath):
        logging.warning("No results found for %s at %s", nickname, filepath)
        return []

    records = []
    if output_format == "csv":
        with open(filepath, "r", encoding="utf-8") as f:
            records.extend(csv.DictReader(f))
    elif output_format == "xlsx":
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = iter(ws.iter_rows(values_only=True))
            first_row = next(rows, None)
            if first_row is not None:
                headers = [str(value) if value is not None else "" for value in first_row]
                for row in rows:
                    records.append(
                        {
                            header: row[index] if index < len(row) and row[index] is not None else ""
                            for index, header in enumerate(headers)
                            if header
                        }
                    )
        finally:
            wb.close()
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    records.sort(key=_record_sort_time)
    return records


def _to_float(value, *, percent: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if percent:
        text = text.removesuffix("%").strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value) -> int | None:
    number = _to_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _quantile(values: list[float], probability: float) -> float:
    """Return a linearly interpolated quantile for a non-empty sample."""
    if not values:
        raise ValueError("Quantile requires at least one value.")
    if not 0 <= probability <= 1:
        raise ValueError("Quantile probability must be between 0 and 1.")

    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def rolling_average(values: list[float | None], window: int) -> list[float | None]:
    """Return a full-window rolling mean, tolerating sparse optional values."""
    if window <= 0:
        raise ValueError("Rolling window must be positive.")

    minimum_count = max(1, math.ceil(window * 0.6))
    result = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
            continue
        current = [value for value in values[index - window + 1 : index + 1] if value is not None]
        result.append(_mean(current) if len(current) >= minimum_count else None)
    return result


def _rolling_weighted_ai(points: list[dict], window: int) -> list[float | None]:
    minimum_count = max(1, math.ceil(window * 0.6))
    result = []
    for index in range(len(points)):
        if index + 1 < window:
            result.append(None)
            continue
        current = points[index - window + 1 : index + 1]
        weighted = [
            (point["aiNumerator"], point["aiDenominator"])
            for point in current
            if point["aiNumerator"] is not None
            and point["aiDenominator"] is not None
            and point["aiDenominator"] > 0
        ]
        if len(weighted) >= minimum_count:
            numerator = sum(pair[0] for pair in weighted)
            denominator = sum(pair[1] for pair in weighted)
            result.append(numerator / denominator * 100 if denominator else None)
            continue
        rates = [point["aiRate"] for point in current if point["aiRate"] is not None]
        result.append(_mean(rates) if len(rates) >= minimum_count else None)
    return result


def _aggregate_rate(
    points: list[dict],
    *,
    rate_key: str,
    numerator_key: str,
    denominator_key: str,
) -> tuple[float | None, int | None, bool]:
    weighted = [
        (point[numerator_key], point[denominator_key])
        for point in points
        if point[numerator_key] is not None
        and point[denominator_key] is not None
        and point[denominator_key] > 0
    ]
    if weighted:
        numerator = sum(pair[0] for pair in weighted)
        denominator = sum(pair[1] for pair in weighted)
        return (numerator / denominator * 100 if denominator else None, denominator, True)

    rates = [point[rate_key] for point in points if point[rate_key] is not None]
    return _mean(rates), None, False


def _build_rating_batches(
    points: list[dict],
    overall_rating_mean: float,
    *,
    batch_size: int = 20,
) -> list[dict]:
    """Build newest-anchored, equal-sized review batches."""
    if batch_size <= 0:
        raise ValueError("Batch size must be positive.")

    ranges = []
    end = len(points)
    while end > 0:
        start = max(0, end - batch_size)
        ranges.append((start, end))
        end = start
    ranges.reverse()

    batches = []
    for batch_index, (start, end) in enumerate(ranges):
        current = points[start:end]
        ratings = [point["rating"] for point in current]
        rating_mean = _mean(ratings)
        ai_rate, ai_denominator, _ = _aggregate_rate(
            current,
            rate_key="aiRate",
            numerator_key="aiNumerator",
            denominator_key="aiDenominator",
        )
        bad_rate_5, bad_denominator_5, _ = _aggregate_rate(
            current,
            rate_key="badRate5",
            numerator_key="badCount5",
            denominator_key="badDenominator",
        )
        bad_rate_10, bad_denominator_10, _ = _aggregate_rate(
            current,
            rate_key="badRate10",
            numerator_key="badCount10",
            denominator_key="badDenominator",
        )
        batches.append(
            {
                "id": f"batch-{batch_index}",
                "startIndex": start,
                "endIndex": end - 1,
                "startLabel": points[start]["label"],
                "endLabel": points[end - 1]["label"],
                "label": f'{points[start]["label"]}–{points[end - 1]["label"]}',
                "count": len(current),
                "ratingMean": rating_mean,
                "ratingDelta": (
                    rating_mean - overall_rating_mean
                    if rating_mean is not None
                    else None
                ),
                "aiRate": ai_rate,
                "aiDenominator": ai_denominator,
                "badRate5": bad_rate_5,
                "badRate10": bad_rate_10,
                "badDenominator": bad_denominator_5 or bad_denominator_10,
            }
        )
    return batches


def _infer_source(record: dict, mode: str) -> str:
    source = str(record.get("source") or "").strip().lower()
    if source in ("majsoul", "tenhou"):
        return source
    if "p-" in mode.lower():
        return "tenhou"
    if mode.isdigit():
        return "majsoul"
    return ""


def _rating_axis_bounds(ratings: list[float]) -> tuple[int, int]:
    minimum = min(ratings)
    maximum = max(ratings)
    if minimum >= 80:
        lower = 80
    elif minimum >= 60:
        lower = 60
    elif minimum >= 40:
        lower = 40
    else:
        lower = 0
    upper = 100 if maximum <= 100 else int(math.ceil(maximum / 10) * 10)
    return lower, upper


def _rate_axis_min(values: list[float]) -> int:
    minimum = min(values)
    if minimum >= 60:
        return 60
    if minimum >= 40:
        return 40
    return 0


def _rate_axis_scale(values: list[float]) -> tuple[float, float]:
    """Return a padded percent axis with four stable, readable intervals."""
    if not values:
        return 10.0, 2.5

    observed_maximum = max(values)
    if observed_maximum <= 0:
        return 1.0, 0.25

    padded_maximum = observed_maximum * 1.1
    magnitude = 10 ** math.floor(math.log10(padded_maximum))
    normalized = padded_maximum / magnitude
    multipliers = (1.0, 1.2, 1.6, 2.0, 2.4, 3.2, 4.0, 5.0, 6.0, 8.0, 10.0)
    multiplier = next(
        candidate for candidate in multipliers if normalized <= candidate
    )
    maximum = multiplier * magnitude
    interval = maximum / 4
    return round(maximum, 10), round(interval, 10)


def _histogram(values: list[float], lower: int, upper: int, bins: int = 10) -> list[dict]:
    width = (upper - lower) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int((value - lower) / width)))
        counts[index] += 1
    return [
        {
            "label": f"{lower + index * width:.0f}–{lower + (index + 1) * width:.0f}",
            "lower": lower + index * width,
            "upper": lower + (index + 1) * width,
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def prepare_dashboard_data(records: list[dict], plot_limit: int | None = None) -> dict | None:
    """Normalize result rows into a single, tested dashboard data contract."""
    selected = records
    if plot_limit is not None and plot_limit > 0 and len(selected) > plot_limit:
        selected = selected[-plot_limit:]

    points = []
    for record in selected:
        rating = _to_float(record.get("rating"))
        if rating is None:
            continue

        ai_numerator = _to_int(record.get("aiConsistencyNumerator"))
        ai_denominator = _to_int(record.get("aiConsistencyDenominator"))
        ai_rate = _to_float(record.get("aiConsistencyRate"), percent=True)
        if ai_numerator is not None and ai_denominator is not None and ai_denominator > 0:
            ai_rate = ai_numerator / ai_denominator * 100

        bad_denominator = _to_int(record.get("badMoveDenominator"))
        bad_count_5 = _to_int(record.get("badMoveCount5"))
        bad_count_10 = _to_int(record.get("badMoveCount10"))
        bad_rate_5 = _to_float(record.get("badMoveRate5"), percent=True)
        bad_rate_10 = _to_float(record.get("badMoveRate10"), percent=True)
        if bad_denominator is not None and bad_denominator > 0:
            if bad_count_5 is not None:
                bad_rate_5 = bad_count_5 / bad_denominator * 100
            if bad_count_10 is not None:
                bad_rate_10 = bad_count_10 / bad_denominator * 100

        mode = str(record.get("mode") or "—")
        started_at = str(record.get("startTime") or "")
        if started_at and not _parse_time(started_at):
            started_at = ""
        points.append(
            {
                "index": len(points) + 1,
                "label": f"#{len(points) + 1}",
                "startedAt": started_at,
                "rating": rating,
                "aiRate": ai_rate,
                "aiNumerator": ai_numerator,
                "aiDenominator": ai_denominator,
                "badRate5": bad_rate_5,
                "badCount5": bad_count_5,
                "badRate10": bad_rate_10,
                "badCount10": bad_count_10,
                "badDenominator": bad_denominator,
                "source": _infer_source(record, mode),
                "mode": mode,
                "modelTag": str(record.get("modelTag") or ""),
                "uuid": str(record.get("uuid") or ""),
                "resultUrl": str(record.get("resultUrl") or ""),
                "paipuUrl": str(record.get("paipuUrl") or ""),
            }
        )

    if not points:
        return None

    ratings = [point["rating"] for point in points]
    rating_mean = _mean(ratings)
    assert rating_mean is not None
    total_games = len(points)
    trend_window = 10 if total_games >= 10 else (5 if total_games >= 8 else None)
    rating_rolling = (
        rolling_average([point["rating"] for point in points], trend_window)
        if trend_window
        else [None] * total_games
    )
    ai_rolling = (
        _rolling_weighted_ai(points, trend_window)
        if trend_window
        else [None] * total_games
    )
    comparison_window = min(20, total_games // 2) if total_games >= 10 else 0
    recent_window = comparison_window or min(20, total_games)
    recent_average = _mean(ratings[-recent_window:])
    previous_average = (
        _mean(ratings[-comparison_window * 2 : -comparison_window])
        if comparison_window
        else None
    )
    comparison_delta = (
        recent_average - previous_average
        if recent_average is not None and previous_average is not None
        else None
    )

    ai_rate, ai_denominator, ai_weighted = _aggregate_rate(
        points,
        rate_key="aiRate",
        numerator_key="aiNumerator",
        denominator_key="aiDenominator",
    )
    bad_rate_5, bad_denominator_5, bad_weighted_5 = _aggregate_rate(
        points,
        rate_key="badRate5",
        numerator_key="badCount5",
        denominator_key="badDenominator",
    )
    bad_rate_10, bad_denominator_10, bad_weighted_10 = _aggregate_rate(
        points,
        rate_key="badRate10",
        numerator_key="badCount10",
        denominator_key="badDenominator",
    )
    rating_axis_min, rating_axis_max = _rating_axis_bounds(ratings)
    ai_values = [point["aiRate"] for point in points if point["aiRate"] is not None]
    ai_axis_min = _rate_axis_min(ai_values) if ai_values else 0
    bad_rate_values = [
        value
        for point in points
        for value in (point["badRate5"], point["badRate10"])
        if value is not None
    ]
    bad_rate_axis_max, bad_rate_axis_interval = _rate_axis_scale(bad_rate_values)
    histogram = (
        _histogram(ratings, rating_axis_min, rating_axis_max)
        if total_games >= 8
        else []
    )
    worst_games = sorted(points, key=lambda point: (point["rating"], point["index"]))[:5]
    highlight_count = min(5, max(1, math.ceil(total_games * 0.05)))
    highlighted = {
        point["index"]
        for point in sorted(points, key=lambda point: (point["rating"], point["index"]))[
            :highlight_count
        ]
    }
    for point in points:
        point["isLow"] = point["index"] in highlighted

    dates = [point["startedAt"] for point in points if point["startedAt"]]
    sources = sorted({point["source"] for point in points if point["source"]})
    modes = sorted({point["mode"] for point in points if point["mode"] and point["mode"] != "—"})
    model_tags = sorted({point["modelTag"] for point in points if point["modelTag"]})

    return {
        "points": points,
        "totalGames": total_games,
        "trendWindow": trend_window,
        "ratingRolling": rating_rolling,
        "ratingMean": rating_mean,
        "aiRolling": ai_rolling,
        "ratingMedian": median(ratings),
        "ratingDenseLower": _quantile(ratings, 0.25),
        "ratingDenseUpper": _quantile(ratings, 0.75),
        "recentWindow": recent_window,
        "recentAverage": recent_average,
        "comparisonWindow": comparison_window,
        "comparisonDelta": comparison_delta,
        "aiRate": ai_rate,
        "aiDenominator": ai_denominator,
        "aiWeighted": ai_weighted,
        "badRate5": bad_rate_5,
        "badRate10": bad_rate_10,
        "badDenominator": bad_denominator_5 or bad_denominator_10,
        "badWeighted": bad_weighted_5 or bad_weighted_10,
        "ratingAxisMin": rating_axis_min,
        "ratingAxisMax": rating_axis_max,
        "aiAxisMin": ai_axis_min,
        "badRateAxisMax": bad_rate_axis_max,
        "badRateAxisInterval": bad_rate_axis_interval,
        "histogram": histogram,
        "worstGames": worst_games,
        "ratingBatches": _build_rating_batches(points, rating_mean),
        "dateStart": dates[0] if dates else "",
        "dateEnd": dates[-1] if dates else "",
        "sources": sources,
        "modes": modes,
        "modelTags": model_tags,
    }


def _format_number(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _display_source(sources: list[str]) -> str:
    labels = {"majsoul": "雀魂", "tenhou": "天凤"}
    return " / ".join(labels.get(source, source) for source in sources) or "数据源未标注"


def _date_range(started_at: str, ended_at: str) -> str:
    start = started_at[:10] if started_at else ""
    end = ended_at[:10] if ended_at else ""
    if not start:
        return "日期未标注"
    return start if start == end or not end else f"{start} — {end}"


def _safe_external_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value)
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


def _worst_game_rows(data: dict) -> str:
    rows = []
    for point in data["worstGames"]:
        link = _safe_external_url(point["resultUrl"]) or _safe_external_url(point["paipuUrl"])
        link_html = (
            f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">打开检讨</a>'
            if link
            else '<span class="muted">无链接</span>'
        )
        rows.append(
            "<tr>"
            f'<td class="mono">#{point["index"]}</td>'
            f'<td>{html.escape(point["startedAt"] or "—")}</td>'
            f'<td><span class="mode-tag">{html.escape(point["mode"])}</span></td>'
            f'<td class="metric strong">{point["rating"]:.2f}</td>'
            f'<td class="metric">{_format_number(point["aiRate"], 1, "%")}</td>'
            f'<td class="metric">{_format_number(point["badRate5"], 1, "%")}</td>'
            f"<td>{link_html}</td>"
            "</tr>"
        )
    return "".join(rows)


def _safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


# Chart map: compact header scorecard for status; focus-and-context raw trends
# with a secondary rolling average; histogram for distribution; exact linked
# table for actionable low-rating review.
REPORT_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>$page_title</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        :root {
            --canvas: #f3f5f8;
            --surface: #ffffff;
            --ink: #172033;
            --muted: #687386;
            --subtle: #97a1b2;
            --line: #e4e8ef;
            --indigo: #3f51c6;
            --indigo-dark: #28388f;
            --indigo-soft: #e4e8fb;
            --amber: #b57d24;
            --amber-soft: #f7ecd8;
            --font: Inter, "SF Pro Display", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            background: var(--canvas);
            color: var(--ink);
            font-family: var(--font);
            -webkit-font-smoothing: antialiased;
        }
        #main {
            width: min(1380px, calc(100% - 40px));
            margin: 28px auto;
            padding: 42px 44px 34px;
            background: var(--surface);
            border: 1px solid var(--line);
            box-shadow: 0 18px 50px rgba(23, 32, 51, .07);
        }
        .report-header {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(460px, 520px);
            align-items: stretch;
            gap: 64px;
            padding-bottom: 28px;
            border-bottom: 1px solid var(--line);
        }
        .report-identity { align-self: center; }
        .eyebrow {
            margin: 0 0 10px;
            color: var(--indigo-dark);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .16em;
            text-transform: uppercase;
        }
        h1 { margin: 0; font-size: clamp(30px, 4vw, 46px); line-height: 1.08; letter-spacing: -.04em; }
        .subtitle { margin: 12px 0 0; color: var(--muted); font-size: 14px; line-height: 1.7; }
        .header-metrics {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            align-self: stretch;
            border-left: 1px solid var(--line);
            border-top: 1px solid var(--line);
        }
        .header-metric {
            position: relative;
            min-width: 0;
            min-height: 88px;
            padding: 15px 18px 12px;
            border-right: 1px solid var(--line);
            border-bottom: 1px solid var(--line);
        }
        .header-metric:nth-child(even) { border-right: 0; }
        .header-metric-primary::before {
            position: absolute;
            top: 14px;
            bottom: 14px;
            left: -1px;
            width: 2px;
            background: var(--indigo);
            content: "";
        }
        .header-metric-primary .header-metric-label { color: var(--indigo-dark); }
        .header-metric-label {
            overflow: hidden;
            color: var(--muted);
            font-size: 11px;
            font-weight: 700;
            letter-spacing: .035em;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .header-metric-value {
            margin-top: 7px;
            font-family: var(--mono);
            font-size: 27px;
            font-weight: 650;
            letter-spacing: -.04em;
            line-height: 1.05;
        }
        .header-metric-note {
            margin-top: 6px;
            color: var(--subtle);
            font-size: 10px;
            line-height: 1.45;
        }
        .header-dual-metric {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 14px;
            margin-top: 7px;
        }
        .header-dual-item + .header-dual-item {
            padding-left: 14px;
            border-left: 1px solid var(--line);
        }
        .header-dual-label { color: var(--muted); font-size: 10px; font-weight: 700; }
        .header-dual-value {
            margin-top: 4px;
            font: 650 23px/1.05 var(--mono);
            letter-spacing: -.04em;
        }
        .delta-up { color: var(--indigo-dark); }
        .delta-down { color: var(--amber); }
        .chart-section { padding: 34px 0 28px; border-bottom: 1px solid var(--line); }
        .section-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 18px; }
        .section-actions { display: flex; align-items: center; gap: 12px; }
        .density-key {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            color: var(--muted);
            font: 600 11px/1 var(--mono);
            white-space: nowrap;
        }
        .density-key-swatch { width: 20px; height: 8px; background: rgba(104, 115, 134, .12); }
        .batch-summary {
            display: grid;
            grid-template-columns: minmax(190px, 1.35fr) repeat(4, minmax(112px, .8fr));
            margin: -2px 0 10px;
            border: 1px solid var(--line);
            background: #fafbfe;
        }
        .batch-summary-cell {
            min-width: 0;
            padding: 11px 14px;
            border-left: 1px solid var(--line);
        }
        .batch-summary-cell:first-child { border-left: 0; }
        .batch-summary-kicker {
            color: var(--subtle);
            font-size: 9px;
            font-weight: 750;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .batch-summary-value {
            margin-top: 4px;
            overflow: hidden;
            color: var(--ink);
            font: 650 15px/1.25 var(--mono);
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .batch-summary-range .batch-summary-value { color: var(--indigo-dark); }
        .batch-summary-note { margin-top: 3px; color: var(--subtle); font-size: 9px; line-height: 1.3; }
        h2 { margin: 0; font-size: 19px; letter-spacing: -.015em; }
        .trend-controls {
            display: inline-flex;
            padding: 3px;
            border: 1px solid var(--line);
            border-radius: 4px;
            background: #f7f8fa;
        }
        .trend-toggle {
            min-width: 54px;
            padding: 6px 12px;
            border: 0;
            background: transparent;
            color: var(--muted);
            font: 650 11px/1 var(--font);
            letter-spacing: .03em;
            cursor: pointer;
        }
        .trend-toggle:hover { color: var(--indigo-dark); }
        .trend-toggle:focus-visible { outline: 2px solid var(--indigo); outline-offset: 2px; }
        .trend-toggle.is-active {
            border-radius: 2px;
            background: var(--surface);
            color: var(--indigo-dark);
            box-shadow: 0 1px 3px rgba(23, 32, 51, .12);
        }
        .selection-clear {
            padding: 6px 10px;
            border: 1px solid var(--line);
            border-radius: 3px;
            background: var(--surface);
            color: var(--muted);
            font: 650 10px/1 var(--font);
            letter-spacing: .03em;
            cursor: pointer;
        }
        .selection-clear:hover { border-color: #c9d0dd; color: var(--indigo-dark); }
        .selection-clear:focus-visible { outline: 2px solid var(--indigo); outline-offset: 2px; }
        .selection-clear[hidden] { display: none; }
        .chart { width: 100%; height: 390px; }
        #rating-chart { height: 460px; }
        .secondary-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr); gap: 38px; }
        .secondary-grid .chart-section { min-width: 0; }
        .secondary-grid .chart { height: 310px; }
        .review-section { padding-top: 34px; }
        .table-wrap { overflow-x: auto; border-top: 1px solid var(--line); }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { padding: 13px 14px; color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .05em; text-align: left; white-space: nowrap; }
        td { padding: 14px; border-top: 1px solid var(--line); white-space: nowrap; }
        tbody tr { transition: background-color .15s ease; }
        tbody tr:hover { background: #fafbff; }
        th:first-child, td:first-child { padding-left: 0; }
        th:last-child, td:last-child { padding-right: 0; text-align: right; }
        .metric, .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
        .strong { color: var(--amber); font-weight: 700; }
        .muted { color: var(--subtle); }
        .mode-tag { display: inline-flex; padding: 4px 8px; color: var(--indigo-dark); background: var(--indigo-soft); font-family: var(--mono); font-size: 11px; }
        a { color: var(--indigo-dark); font-weight: 650; text-decoration: none; }
        a:hover { text-decoration: underline; }
        footer { display: flex; justify-content: space-between; gap: 24px; margin-top: 34px; color: var(--subtle); font-size: 11px; line-height: 1.6; }
        .chart-error { display: grid; place-items: center; height: 100%; color: var(--muted); background: #fafbfc; }
        @media (max-width: 980px) {
            #main { width: 100%; margin: 0; padding: 28px 24px; border: 0; }
            .report-header { grid-template-columns: 1fr; gap: 22px; }
            .header-metric { min-height: 84px; padding: 13px 14px 11px; }
            .secondary-grid { grid-template-columns: 1fr; gap: 0; }
            .section-head { flex-direction: column; gap: 8px; }
            .section-actions { width: 100%; justify-content: space-between; }
            .batch-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .batch-summary-cell { border-top: 1px solid var(--line); }
            .batch-summary-cell:nth-child(odd) { border-left: 0; }
            .batch-summary-cell:first-child, .batch-summary-cell:nth-child(2) { border-top: 0; }
            .batch-summary-range { grid-column: 1 / -1; }
            .batch-summary-cell:nth-child(2) { border-top: 1px solid var(--line); border-left: 0; }
            footer { flex-direction: column; }
        }
        @media (max-width: 560px) {
            .header-metric-label { font-size: 10px; }
            .header-metric-value { font-size: 23px; }
            .header-dual-metric { gap: 9px; }
            .header-dual-item + .header-dual-item { padding-left: 9px; }
            .header-dual-value { font-size: 20px; }
            .chart { height: 340px; }
            #rating-chart { height: 430px; }
        }
    </style>
</head>
<body>
<main id="main">
    <header class="report-header">
        <div class="report-identity">
            <p class="eyebrow">Mortal analysis report</p>
            <h1>$nickname</h1>
            <p class="subtitle">$metadata</p>
        </div>
        <section class="header-metrics header-metrics-grid" aria-label="关键指标">
            <article class="header-metric header-metric-primary">
                <div class="header-metric-label">全样本 Rating 平均值</div>
                <div class="header-metric-value">$rating_mean</div>
                <div class="header-metric-note">$total_games 半庄</div>
            </article>
            <article class="header-metric">
                <div class="header-metric-label">Rating 中位数</div>
                <div class="header-metric-value">$rating_median</div>
                <div class="header-metric-note">全样本</div>
            </article>
            <article class="header-metric">
                <div class="header-metric-label">$ai_label</div>
                <div class="header-metric-value">$ai_rate</div>
                <div class="header-metric-note">$ai_note</div>
            </article>
            <article class="header-metric">
                <div class="header-metric-label">恶手率</div>
                <div class="header-dual-metric">
                    <div class="header-dual-item">
                        <div class="header-dual-label">5% 恶手</div>
                        <div class="header-dual-value">$bad_rate_5</div>
                    </div>
                    <div class="header-dual-item">
                        <div class="header-dual-label">10% 恶手</div>
                        <div class="header-dual-value">$bad_rate_10</div>
                    </div>
                </div>
                <div class="header-metric-note">$bad_sample_note</div>
            </article>
        </section>
    </header>

    <section class="chart-section">
        <div class="section-head">
            <div>
                <h2>Rating 推移</h2>
            </div>
            <div class="section-actions">
                <div class="density-key"><span class="density-key-swatch"></span>Rating 中间50% $rating_dense_range</div>
                <div class="trend-controls" role="group" aria-label="趋势图数据密度">
                    <button type="button" class="trend-toggle is-active" data-trend-view="overview" aria-pressed="true">概览</button>
                    <button type="button" class="trend-toggle" data-trend-view="detail" aria-pressed="false">明细</button>
                </div>
            </div>
        </div>
        <div id="rating-batch-summary" class="batch-summary" aria-live="polite"></div>
        <div id="rating-chart" class="chart" role="img" aria-label="Rating、5% 与 10% 恶手率推移"></div>
    </section>

    <div class="secondary-grid">
        $ai_section
        $distribution_section
    </div>

    <section class="review-section">
        <div class="section-head">
            <div>
                <h2>检讨候选</h2>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <thead><tr><th>序号</th><th>开局时间</th><th>模式</th><th>Rating</th><th>AI 一致率</th><th>5% 恶手率</th><th>操作</th></tr></thead>
                <tbody>$worst_rows</tbody>
            </table>
        </div>
    </section>

    <footer>
        <span>Rating 方差较大，仅用于筛选何切检讨，不代表牌力。</span>
        <span>生成自 Batch Mortal · 数据截至 $date_end</span>
    </footer>
</main>

<script>
const report = $payload;
const colors = {
    ink: '#172033', muted: '#687386', subtle: '#97a1b2', line: '#e4e8ef',
    indigo: '#3f51c6', indigoDark: '#28388f', indigoSoft: '#cbd3f5',
    amber: '#b57d24', amberSoft: '#f7ecd8', axis: '#cfd6e2', grid: '#edf0f5'
};
const ratingSelectionPalette = [
    '#28388f', '#b57d24', '#7b526f', '#5f713f', '#2f6f78',
    '#5268bf', '#c26b32', '#9a6188', '#7b874f', '#4f8990'
];
const charts = [];
const trendCharts = [];
let trendViewMode = 'overview';
let zoomRevealsDetail = false;
let ratingChart = null;
let aiChart = null;
let selectedRatingBinIndexes = [];
let selectedBatchIndex = Math.max(0, report.ratingBatches.length - 1);

function escapeHtml(value) {
    const map = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#039;'};
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(char) { return map[char]; });
}
function formatNumber(value, digits, suffix) {
    return value == null ? '—' : Number(value).toFixed(digits) + (suffix || '');
}
function axisTooltip(params) {
    const item = params.find(function(param) { return param.dataIndex != null; });
    if (!item) return '';
    const point = report.points[item.dataIndex];
    let content = '<div style="min-width:220px">';
    content += '<div style="font-weight:700;color:' + colors.ink + ';margin-bottom:9px">' + escapeHtml(point.label) + ' · ' + escapeHtml(point.startedAt || '时间未标注') + '</div>';
    content += '<div style="display:flex;justify-content:space-between;gap:24px"><span style="color:' + colors.muted + '">Rating</span><b>' + formatNumber(point.rating, 2) + '</b></div>';
    content += '<div style="display:flex;justify-content:space-between;gap:24px;margin-top:5px"><span style="color:' + colors.muted + '">AI 一致率</span><b>' + formatNumber(point.aiRate, 1, '%') + '</b></div>';
    if (point.aiDenominator != null) {
        content += '<div style="color:' + colors.subtle + ';font-size:11px;text-align:right">' + escapeHtml(point.aiNumerator) + ' / ' + escapeHtml(point.aiDenominator) + ' 次决策</div>';
    }
    content += '<div style="display:flex;justify-content:space-between;gap:24px;margin-top:5px"><span style="color:' + colors.muted + '">5% 恶手率</span><b>' + formatNumber(point.badRate5, 1, '%') + '</b></div>';
    content += '<div style="display:flex;justify-content:space-between;gap:24px;margin-top:5px"><span style="color:' + colors.muted + '">10% 恶手率</span><b>' + formatNumber(point.badRate10, 1, '%') + '</b></div>';
    if (point.badDenominator != null) {
        content += '<div style="color:' + colors.subtle + ';font-size:11px;text-align:right">' + escapeHtml(point.badDenominator) + ' 次决策</div>';
    }
    content += '<div style="margin-top:8px;color:' + colors.subtle + ';font-size:11px">模式 ' + escapeHtml(point.mode) + '</div></div>';
    return content;
}
function xAxisConfig() {
    const step = Math.max(1, Math.ceil(report.totalGames / 12));
    return {
        type: 'category',
        boundaryGap: false,
        data: report.points.map(function(point) { return point.label; }),
        axisLine: { lineStyle: { color: colors.axis, width: 1 } },
        axisTick: { show: false },
        axisLabel: {
            color: colors.subtle,
            fontSize: 11,
            fontFamily: '"SFMono-Regular", Consolas, monospace',
            margin: 12,
            interval: function(index) { return index === report.totalGames - 1 || index % step === 0; }
        }
    };
}
function badRateXAxisConfig() {
    return {
        type: 'category',
        gridIndex: 1,
        boundaryGap: true,
        data: report.points.map(function(point) { return point.label; }),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false }
    };
}
function zoomConfig(includeBadRate) {
    if (report.totalGames <= 50) return [];
    const axisIndexes = includeBadRate ? [0, 1] : [0];
    return [
        { type: 'inside', xAxisIndex: axisIndexes, start: 0, end: 100 },
        {
            type: 'slider', xAxisIndex: axisIndexes, start: 0, end: 100, height: 12, bottom: 10,
            showDetail: false, brushSelect: false, moveHandleSize: 4,
            borderColor: 'transparent', backgroundColor: '#f4f6f9', fillerColor: '#dfe4f8',
            dataBackground: {
                lineStyle: { color: '#b7c1e8', width: 1 },
                areaStyle: { color: '#eef1fb', opacity: 1 }
            },
            selectedDataBackground: {
                lineStyle: { color: colors.indigo, width: 1 },
                areaStyle: { color: '#dfe4f8', opacity: 1 }
            },
            handleSize: 14,
            handleStyle: { color: '#ffffff', borderColor: colors.indigo, borderWidth: 1.5 },
            emphasis: { handleStyle: { color: colors.indigoSoft, borderColor: colors.indigoDark } },
            textStyle: { color: colors.subtle }
        }
    ];
}
function ratingContextArea() {
    const data = report.ratingBatches.map(function(batch, index) {
        const selected = index === selectedBatchIndex;
        const nextBatch = report.ratingBatches[index + 1];
        return [
            {
                name: batch.id,
                xAxis: batch.startLabel,
                itemStyle: {
                    color: selected
                        ? 'rgba(63, 81, 198, .095)'
                        : (index % 2 === 0 ? 'rgba(63, 81, 198, .018)' : 'rgba(104, 115, 134, .035)'),
                    borderColor: selected ? 'rgba(63, 81, 198, .32)' : 'transparent',
                    borderWidth: selected ? 1 : 0
                }
            },
            { xAxis: nextBatch ? nextBatch.startLabel : batch.endLabel }
        ];
    });
    return {
        silent: false,
        label: { show: false },
        emphasis: { itemStyle: { color: 'rgba(63, 81, 198, .13)' } },
        data: data
    };
}
function ratingDensityArea() {
    const data = [];
    if (report.ratingDenseLower != null && report.ratingDenseUpper != null) {
        data.push([
            {
                name: '全样本中间50%',
                yAxis: report.ratingDenseLower,
                itemStyle: { color: 'rgba(104, 115, 134, .075)', borderWidth: 0 }
            },
            { yAxis: report.ratingDenseUpper }
        ]);
    }
    return {
        silent: true,
        label: { show: false },
        data: data
    };
}
function trendReferenceLine(value, label) {
    const data = [];
    if (value != null) {
        data.push({
            yAxis: value,
            lineStyle: { color: colors.subtle, type: 'dashed', width: 1 },
            label: {
                show: true,
                formatter: label,
                position: 'insideStartTop',
                color: colors.muted,
                fontSize: 10
            }
        });
    }
    return { silent: true, symbol: 'none', data: data };
}
function rawEndLabel(digits, suffix) {
    return {
        show: true,
        formatter: function(params) { return formatNumber(params.value, digits, suffix); },
        color: colors.indigoDark,
        fontFamily: '"SFMono-Regular", Consolas, monospace',
        fontSize: 10,
        fontWeight: 700,
        backgroundColor: 'rgba(255,255,255,.92)',
        borderColor: colors.line,
        borderWidth: 1,
        padding: [3, 5],
        borderRadius: 2,
        distance: 7
    };
}
function trendAverageName() {
    return report.trendWindow + '半庄移动平均';
}
function rawSeriesData(valueKey) {
    return report.points.map(function(point) { return point[valueKey]; });
}
function badRateBarData(valueKey, threshold) {
    const batch = report.ratingBatches[selectedBatchIndex];
    return report.points.map(function(point, index) {
        if (point[valueKey] == null) return null;
        const isStrict = threshold === 5;
        const selected = batch && index >= batch.startIndex && index <= batch.endIndex;
        return {
            value: point[valueKey],
            itemStyle: {
                color: isStrict
                    ? (selected ? 'rgba(159, 99, 26, .84)' : 'rgba(159, 99, 26, .28)')
                    : (selected ? 'rgba(209, 169, 104, .68)' : 'rgba(209, 169, 104, .18)')
            }
        };
    });
}
function selectedRawSeriesData(valueKey) {
    const batch = report.ratingBatches[selectedBatchIndex];
    const startIndex = Math.max(0, batch.startIndex - 1);
    return report.points.map(function(point, index) {
        return index < startIndex || index > batch.endIndex ? null : point[valueKey];
    });
}
function shouldRevealAllPoints() {
    return report.totalGames <= 30 || trendViewMode === 'detail' || zoomRevealsDetail;
}
function rawPointData(valueKey, revealAll, excludeLow) {
    const batch = report.ratingBatches[selectedBatchIndex];
    return report.points.map(function(point, index) {
        const value = point[valueKey];
        const visible = revealAll || (index >= batch.startIndex && index <= batch.endIndex);
        if (value == null || !visible || (excludeLow && point.isLow)) return null;
        return {
            value: value,
            symbol: 'circle',
            symbolSize: 6,
            itemStyle: {
                color: '#ffffff',
                borderColor: colors.indigo,
                borderWidth: 1.35,
                opacity: .82
            }
        };
    });
}
function lowRatingPointData(valueKey) {
    return report.points.map(function(point) {
        const value = point[valueKey];
        if (value == null || !point.isLow) return null;
        return {
            value: value,
            symbol: 'diamond',
            symbolSize: 10,
            itemStyle: {
                color: colors.amberSoft,
                borderColor: colors.amber,
                borderWidth: 1.8,
                opacity: 1
            }
        };
    });
}
function refreshTrendPoints() {
    const revealAll = shouldRevealAllPoints();
    trendCharts.forEach(function(entry) {
        entry.chart.setOption({
            series: [{
                id: entry.seriesId,
                data: rawPointData(entry.valueKey, revealAll, entry.excludeLow)
            }]
        });
    });
}
function ratingBinHighlightData() {
    if (!selectedRatingBinIndexes.length) return [];
    return report.points.map(function(point) {
        const selectionPosition = selectedRatingBinIndexes.findIndex(function(binIndex) {
            const bin = report.histogram[binIndex];
            const isLast = binIndex === report.histogram.length - 1;
            return point.rating >= bin.lower && (point.rating < bin.upper || (isLast && point.rating <= bin.upper));
        });
        if (selectionPosition < 0) return null;
        const color = ratingSelectionPalette[selectionPosition % ratingSelectionPalette.length];
        return {
            value: point.rating,
            symbol: 'circle',
            symbolSize: 12,
            itemStyle: {
                color: '#ffffff',
                borderColor: color,
                borderWidth: 2,
                opacity: 1
            }
        };
    });
}
function refreshRatingBinHighlight() {
    if (!ratingChart) return;
    ratingChart.setOption({
        series: [{ id: 'rating-bin-highlight', data: ratingBinHighlightData() }]
    });
}
function updateRatingSelectionControl() {
    const button = document.getElementById('rating-selection-clear');
    if (button) button.hidden = !selectedRatingBinIndexes.length;
}
function distributionBarData() {
    return report.histogram.map(function(bin, index) {
        const selectionPosition = selectedRatingBinIndexes.indexOf(index);
        const selected = selectionPosition >= 0;
        const color = selected
            ? ratingSelectionPalette[selectionPosition % ratingSelectionPalette.length]
            : colors.indigoSoft;
        return {
            value: bin.count,
            itemStyle: {
                color: color,
                borderColor: selected ? color : colors.indigo,
                borderWidth: selected ? 0 : 1,
                opacity: !selectedRatingBinIndexes.length || selected ? 1 : .34
            },
            label: { color: selected ? color : colors.muted, fontWeight: selected ? 750 : 500 }
        };
    });
}
function renderRatingBatchSummary() {
    const element = document.getElementById('rating-batch-summary');
    const batch = report.ratingBatches[selectedBatchIndex];
    if (!element || !batch) return;
    const delta = batch.ratingDelta == null
        ? '—'
        : (batch.ratingDelta >= 0 ? '+' : '−') + Math.abs(batch.ratingDelta).toFixed(2);
    const aiNote = batch.aiDenominator ? batch.aiDenominator + ' 次决策' : '批内有值样本';
    const badNote = batch.badDenominator ? batch.badDenominator + ' 次决策' : '批内有值样本';
    element.innerHTML =
        '<div class="batch-summary-cell batch-summary-range">' +
            '<div class="batch-summary-kicker">当前批次 · 每 20 半庄</div>' +
            '<div class="batch-summary-value">' + escapeHtml(batch.label) + '</div>' +
            '<div class="batch-summary-note">' + batch.count + ' 半庄 · 点击图中底色切换</div>' +
        '</div>' +
        '<div class="batch-summary-cell">' +
            '<div class="batch-summary-kicker">Rating 均值</div>' +
            '<div class="batch-summary-value">' + formatNumber(batch.ratingMean, 2) + '</div>' +
            '<div class="batch-summary-note">较全样本 ' + delta + '</div>' +
        '</div>' +
        '<div class="batch-summary-cell">' +
            '<div class="batch-summary-kicker">AI 一致率</div>' +
            '<div class="batch-summary-value">' + formatNumber(batch.aiRate, 1, '%') + '</div>' +
            '<div class="batch-summary-note">' + aiNote + '</div>' +
        '</div>' +
        '<div class="batch-summary-cell">' +
            '<div class="batch-summary-kicker">5% 恶手率</div>' +
            '<div class="batch-summary-value">' + formatNumber(batch.badRate5, 1, '%') + '</div>' +
            '<div class="batch-summary-note">' + badNote + '</div>' +
        '</div>' +
        '<div class="batch-summary-cell">' +
            '<div class="batch-summary-kicker">10% 恶手率</div>' +
            '<div class="batch-summary-value">' + formatNumber(batch.badRate10, 1, '%') + '</div>' +
            '<div class="batch-summary-note">' + badNote + '</div>' +
        '</div>';
}
function refreshBatchSelection() {
    if (ratingChart) {
        const series = [
            { id: 'rating-raw', markArea: ratingContextArea() },
            { id: 'rating-selected-raw', data: selectedRawSeriesData('rating') }
        ];
        if (report.badRate5 != null || report.badRate10 != null) {
            series.push(
                { id: 'rating-bad-rate-10-bars', data: badRateBarData('badRate10', 10) },
                { id: 'rating-bad-rate-5-bars', data: badRateBarData('badRate5', 5) }
            );
        }
        ratingChart.setOption({
            series: series
        });
    }
    if (aiChart) {
        aiChart.setOption({
            series: [
                { id: 'ai-raw', markArea: ratingContextArea() },
                { id: 'ai-selected-raw', data: selectedRawSeriesData('aiRate') }
            ]
        });
    }
}
function updateTrendControls() {
    document.querySelectorAll('[data-trend-view]').forEach(function(button) {
        const active = button.dataset.trendView === trendViewMode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}
function bindTrendViewControls() {
    const controls = document.querySelectorAll('.trend-controls');
    if (report.totalGames <= 30) {
        controls.forEach(function(control) { control.hidden = true; });
        return;
    }
    document.querySelectorAll('[data-trend-view]').forEach(function(button) {
        button.addEventListener('click', function() {
            trendViewMode = button.dataset.trendView;
            updateTrendControls();
            refreshTrendPoints();
        });
    });
}
function bindZoomDetail(chart) {
    chart.on('datazoom', function(params) {
        const event = params.batch && params.batch.length ? params.batch[0] : params;
        if (event.start == null || event.end == null) return;
        const visibleCount = Math.ceil(report.totalGames * Math.max(0, event.end - event.start) / 100);
        const reveal = visibleCount <= 30;
        if (reveal === zoomRevealsDetail) return;
        zoomRevealsDetail = reveal;
        refreshTrendPoints();
    });
}
function bindReviewLink(chart) {
    chart.on('click', function(params) {
        if (params.componentType !== 'series' || params.dataIndex == null) return;
        const point = report.points[params.dataIndex];
        const target = point.resultUrl || point.paipuUrl;
        if (/^https?:\/\//i.test(target)) window.open(target, '_blank', 'noopener');
    });
}
function bindBatchSelection(chart) {
    chart.on('click', function(params) {
        if (params.componentType !== 'markArea') return;
        const name = params.name || (params.data && params.data.name) || '';
        const index = report.ratingBatches.findIndex(function(batch) { return batch.id === name; });
        if (index < 0 || index === selectedBatchIndex) return;
        selectedBatchIndex = index;
        trendViewMode = 'detail';
        updateTrendControls();
        refreshTrendPoints();
        renderRatingBatchSummary();
        refreshBatchSelection();
    });
}
function renderRatingChart() {
    const chart = echarts.init(document.getElementById('rating-chart'));
    ratingChart = chart;
    chart.group = 'batchmortal-trends';
    const hasBadRate = report.badRate5 != null || report.badRate10 != null;
    const series = [{
        id: 'rating-raw',
        name: '单半庄',
        type: 'line',
        data: rawSeriesData('rating'),
        symbol: 'none',
        smooth: .08,
        smoothMonotone: 'x',
        connectNulls: false,
        lineStyle: {
            color: colors.indigo, width: 1.05, opacity: .66,
            cap: 'round', join: 'round'
        },
        itemStyle: { color: colors.indigo },
        emphasis: { lineStyle: { width: 1.7, opacity: .95 } },
        markLine: trendReferenceLine(
            report.ratingMedian,
            '中位数 ' + Number(report.ratingMedian).toFixed(1)
        ),
        markArea: ratingContextArea(),
        z: 4
    }];
    series.push({
        id: 'rating-density-band',
        name: 'rating-density-band',
        type: 'line',
        data: report.points.map(function() { return null; }),
        symbol: 'none',
        silent: true,
        tooltip: { show: false },
        lineStyle: { opacity: 0 },
        markArea: ratingDensityArea(),
        z: 0
    });
    if (report.trendWindow) {
        series.push({
            id: 'rating-average', name: trendAverageName(), type: 'line', data: report.ratingRolling,
            symbol: 'none', connectNulls: false, smooth: false,
            lineStyle: { color: colors.subtle, width: 1.25, type: 'dashed', opacity: .62 },
            itemStyle: { color: colors.subtle },
            emphasis: { lineStyle: { width: 1.75, opacity: .95 } },
            z: 3
        });
    }
    if (hasBadRate) {
        series.push({
            id: 'rating-bad-rate-10-bars',
            name: '10% 恶手率',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: badRateBarData('badRate10', 10),
            barMaxWidth: 7,
            itemStyle: {
                color: 'rgba(209, 169, 104, .34)',
                borderColor: 'rgba(181, 125, 36, .42)',
                borderWidth: .6
            },
            emphasis: { itemStyle: { color: 'rgba(209, 169, 104, .7)' } },
            z: 1
        });
        series.push({
            id: 'rating-bad-rate-5-bars',
            name: '5% 恶手率',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: badRateBarData('badRate5', 5),
            barMaxWidth: 7,
            barGap: '-100%',
            itemStyle: { color: colors.amber },
            emphasis: { itemStyle: { color: colors.amber, opacity: .9 } },
            z: 2
        });
    }
    series.push({
        id: 'rating-selected-raw',
        name: 'rating-selected-raw',
        type: 'line',
        data: selectedRawSeriesData('rating'),
        symbol: 'none',
        smooth: .08,
        smoothMonotone: 'x',
        connectNulls: false,
        silent: true,
        tooltip: { show: false },
        lineStyle: {
            color: colors.indigoDark, width: 1.65, opacity: .96,
            cap: 'round', join: 'round'
        },
        itemStyle: { color: colors.indigoDark },
        endLabel: rawEndLabel(2, ''),
        labelLayout: { moveOverlap: 'shiftY' },
        emphasis: { disabled: true },
        z: 5
    });
    series.push({
        id: 'rating-points',
        name: '单半庄',
        type: 'scatter',
        data: rawPointData('rating', shouldRevealAllPoints(), true),
        itemStyle: { color: '#ffffff', borderColor: colors.indigo, borderWidth: 1.35 },
        emphasis: {
            scale: 1.55,
            itemStyle: { opacity: 1, borderWidth: 2 }
        },
        z: 6
    });
    series.push({
        id: 'rating-low-points',
        name: '低 Rating 局',
        type: 'scatter',
        data: lowRatingPointData('rating'),
        symbol: 'diamond',
        symbolSize: 10,
        itemStyle: { color: colors.amberSoft, borderColor: colors.amber, borderWidth: 1.8 },
        emphasis: { scale: 1.55, itemStyle: { opacity: 1, borderWidth: 2.2 } },
        z: 7
    });
    series.push({
        id: 'rating-bin-highlight',
        name: 'Rating 分布选中',
        type: 'scatter',
        data: ratingBinHighlightData(),
        symbol: 'circle',
        symbolSize: 12,
        tooltip: { show: false },
        itemStyle: { color: '#ffffff', borderColor: colors.indigoDark, borderWidth: 2 },
        emphasis: { scale: 1.12, itemStyle: { borderWidth: 2, opacity: 1 } },
        z: 9
    });
    const ratingYAxis = {
        type: 'value', gridIndex: 0, min: report.ratingAxisMin, max: report.ratingAxisMax,
        axisLine: { show: true, lineStyle: { color: colors.axis } }, axisTick: { show: false },
        axisLabel: { color: colors.subtle, fontSize: 11, fontFamily: '"SFMono-Regular", Consolas, monospace' },
        splitLine: { lineStyle: { color: colors.grid, type: 'solid' } }
    };
    const ratingLegend = report.trendWindow
        ? ['单半庄', '低 Rating 局', trendAverageName()]
        : ['单半庄', '低 Rating 局'];
    if (hasBadRate) ratingLegend.push('10% 恶手率', '5% 恶手率');
    chart.setOption({
        animation: false,
        textStyle: { fontFamily: 'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' },
        tooltip: { trigger: 'axis', confine: true, axisPointer: { type: 'line', lineStyle: { color: colors.axis, width: 1 } }, backgroundColor: '#ffffff', borderColor: colors.line, borderWidth: 1, padding: 12, formatter: axisTooltip },
        axisPointer: hasBadRate ? { link: [{ xAxisIndex: [0, 1] }] } : {},
        legend: {
            top: 0, right: 0,
            data: ratingLegend,
            itemWidth: 16, itemHeight: 7, itemGap: 16,
            textStyle: { color: colors.muted, fontSize: 11 }
        },
        grid: hasBadRate ? [
            { left: 54, right: 62, top: 44, bottom: report.totalGames > 50 ? 158 : 136 },
            { left: 54, right: 62, height: 72, bottom: report.totalGames > 50 ? 50 : 26 }
        ] : [{ left: 54, right: 62, top: 44, bottom: report.totalGames > 50 ? 60 : 42 }],
        xAxis: hasBadRate ? [xAxisConfig(), badRateXAxisConfig()] : xAxisConfig(),
        yAxis: hasBadRate ? [ratingYAxis, {
            type: 'value', gridIndex: 1, position: 'left', min: 0, max: report.badRateAxisMax,
            interval: report.badRateAxisInterval,
            axisLine: { show: true, lineStyle: { color: '#decfb9' } }, axisTick: { show: false },
            axisLabel: {
                color: colors.subtle,
                fontSize: 9,
                fontFamily: '"SFMono-Regular", Consolas, monospace',
                hideOverlap: false,
                formatter: function(value) {
                    const digits = Math.abs(value - Math.round(value)) < 1e-8 ? 0 : 2;
                    return Number(value).toFixed(digits).replace(/\.00$$/, '') + '%';
                }
            },
            splitLine: { lineStyle: { color: '#f0e9dc', type: 'dashed' } }
        }] : ratingYAxis,
        dataZoom: zoomConfig(hasBadRate),
        series: series
    });
    bindReviewLink(chart);
    bindZoomDetail(chart);
    bindBatchSelection(chart);
    trendCharts.push({
        chart: chart, seriesId: 'rating-points', valueKey: 'rating', excludeLow: true
    });
    charts.push(chart);
}
function renderAiChart() {
    const element = document.getElementById('ai-chart');
    if (!element) return;
    const chart = echarts.init(element);
    aiChart = chart;
    chart.group = 'batchmortal-trends';
    const series = [{
        id: 'ai-raw',
        name: '单半庄',
        type: 'line',
        data: rawSeriesData('aiRate'),
        symbol: 'none',
        smooth: .08,
        smoothMonotone: 'x',
        connectNulls: false,
        lineStyle: {
            color: colors.indigo, width: 1, opacity: .66,
            cap: 'round', join: 'round'
        },
        itemStyle: { color: colors.indigo },
        emphasis: { lineStyle: { width: 1.65, opacity: .95 } },
        markLine: trendReferenceLine(report.aiRate, '总体 ' + formatNumber(report.aiRate, 1, '%')),
        markArea: ratingContextArea(),
        z: 4
    }];
    if (report.trendWindow) {
        series.push({
            id: 'ai-average', name: trendAverageName(), type: 'line', data: report.aiRolling,
            symbol: 'none', smooth: false, connectNulls: false,
            lineStyle: { color: colors.subtle, width: 1.2, type: 'dashed', opacity: .62 },
            itemStyle: { color: colors.subtle },
            emphasis: { lineStyle: { width: 1.7, opacity: .95 } },
            z: 3
        });
    }
    series.push({
        id: 'ai-selected-raw',
        name: 'ai-selected-raw',
        type: 'line',
        data: selectedRawSeriesData('aiRate'),
        symbol: 'none',
        smooth: .08,
        smoothMonotone: 'x',
        connectNulls: false,
        silent: true,
        tooltip: { show: false },
        lineStyle: {
            color: colors.indigoDark, width: 1.55, opacity: .96,
            cap: 'round', join: 'round'
        },
        itemStyle: { color: colors.indigoDark },
        endLabel: rawEndLabel(1, '%'),
        labelLayout: { moveOverlap: 'shiftY' },
        emphasis: { disabled: true },
        z: 5
    });
    series.push({
        id: 'ai-points',
        name: '单半庄', type: 'scatter',
        data: rawPointData('aiRate', shouldRevealAllPoints(), false),
        itemStyle: { color: '#ffffff', borderColor: colors.indigo, borderWidth: 1.35 },
        emphasis: { scale: 1.55, itemStyle: { opacity: 1, borderWidth: 2 } },
        z: 6
    });
    chart.setOption({
        animation: false,
        tooltip: { trigger: 'axis', confine: true, axisPointer: { type: 'line', lineStyle: { color: colors.axis, width: 1 } }, backgroundColor: '#ffffff', borderColor: colors.line, borderWidth: 1, padding: 12, formatter: axisTooltip },
        legend: {
            top: 0, right: 0,
            data: report.trendWindow ? ['单半庄', trendAverageName()] : ['单半庄'],
            itemWidth: 16, itemHeight: 7, itemGap: 16,
            textStyle: { color: colors.muted, fontSize: 11 }
        },
        grid: { left: 48, right: 58, top: 44, bottom: 38 },
        xAxis: xAxisConfig(),
        yAxis: {
            type: 'value', min: report.aiAxisMin, max: 100,
            axisLine: { show: true, lineStyle: { color: colors.axis } }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 11, formatter: '{value}%', fontFamily: '"SFMono-Regular", Consolas, monospace' },
            splitLine: { lineStyle: { color: colors.grid, type: 'solid' } }
        },
        dataZoom: report.totalGames > 50 ? [{ type: 'inside', start: 0, end: 100 }] : [],
        series: series
    });
    bindReviewLink(chart);
    bindZoomDetail(chart);
    bindBatchSelection(chart);
    trendCharts.push({
        chart: chart, seriesId: 'ai-points', valueKey: 'aiRate', excludeLow: false
    });
    charts.push(chart);
}
function renderDistributionChart() {
    const element = document.getElementById('distribution-chart');
    if (!element) return;
    const chart = echarts.init(element);
    chart.setOption({
        animation: false,
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            backgroundColor: '#ffffff',
            borderColor: colors.line,
            borderWidth: 1,
            formatter: function(params) {
                const item = params[0];
                const bin = report.histogram[item.dataIndex];
                return '<b>Rating ' + escapeHtml(bin.label) + '</b><br><span style="color:' + colors.muted + '">' + bin.count + ' 半庄 · 再次点击取消 · Ctrl + 点击多选</span>';
            }
        },
        grid: { left: 40, right: 12, top: 18, bottom: 52 },
        xAxis: {
            type: 'category', data: report.histogram.map(function(bin) { return bin.label; }),
            axisLine: { lineStyle: { color: colors.line } }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 10, rotate: 35 }
        },
        yAxis: {
            type: 'value', minInterval: 1,
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 10 },
            splitLine: { lineStyle: { color: colors.line, type: 'dashed' } }
        },
        series: [{
            id: 'rating-distribution-bars',
            name: '对局数', type: 'bar', data: distributionBarData(),
            barMaxWidth: 28,
            itemStyle: { color: colors.indigoSoft, borderColor: colors.indigo, borderWidth: 1 },
            label: { show: true, position: 'top', color: colors.muted, fontSize: 10 }
        }]
    });
    chart.on('click', function(params) {
        if (params.componentType !== 'series' || params.seriesId !== 'rating-distribution-bars') return;
        const sourceEvent = params.event && (params.event.event || params.event);
        const additive = Boolean(sourceEvent && (sourceEvent.ctrlKey || sourceEvent.metaKey));
        const existingPosition = selectedRatingBinIndexes.indexOf(params.dataIndex);
        if (additive) {
            if (existingPosition >= 0) {
                selectedRatingBinIndexes.splice(existingPosition, 1);
            } else {
                selectedRatingBinIndexes.push(params.dataIndex);
            }
        } else if (selectedRatingBinIndexes.length === 1 && existingPosition === 0) {
            selectedRatingBinIndexes = [];
        } else {
            selectedRatingBinIndexes = [params.dataIndex];
        }
        trendViewMode = 'detail';
        updateTrendControls();
        refreshTrendPoints();
        refreshRatingBinHighlight();
        chart.setOption({
            series: [{ id: 'rating-distribution-bars', data: distributionBarData() }]
        });
        updateRatingSelectionControl();
    });
    const clearButton = document.getElementById('rating-selection-clear');
    if (clearButton) {
        clearButton.addEventListener('click', function() {
            selectedRatingBinIndexes = [];
            refreshRatingBinHighlight();
            chart.setOption({
                series: [{ id: 'rating-distribution-bars', data: distributionBarData() }]
            });
            updateRatingSelectionControl();
        });
    }
    updateRatingSelectionControl();
    charts.push(chart);
}
function renderAll() {
    renderRatingBatchSummary();
    if (typeof echarts === 'undefined') {
        document.querySelectorAll('.chart').forEach(function(element) {
            element.innerHTML = '<div class="chart-error">图表资源加载失败；关键指标和检讨表仍可使用。</div>';
        });
        window.__BATCHMORTAL_READY__ = true;
        return;
    }
    renderRatingChart();
    renderAiChart();
    renderDistributionChart();
    bindTrendViewControls();
    echarts.connect('batchmortal-trends');
    window.addEventListener('resize', function() { charts.forEach(function(chart) { chart.resize(); }); });
    window.__BATCHMORTAL_READY__ = true;
}
renderAll();
</script>
</body>
</html>
""")


def generate_html(
    nickname: str,
    output_path: str,
    format_type: str = "xlsx",
    plot_limit: int | None = None,
    results_root: str | None = None,
) -> str | None:
    data = prepare_dashboard_data(
        read_results(nickname, format_type, output_root=results_root),
        plot_limit=plot_limit,
    )
    if not data:
        logging.warning("No valid rating data found to plot.")
        return None

    source_label = _display_source(data["sources"])
    mode_label = ", ".join(data["modes"]) or "模式未标注"
    model_label = ", ".join(data["modelTags"]) or "模型未标注"
    metadata = " · ".join(
        [
            _date_range(data["dateStart"], data["dateEnd"]),
            f'{data["totalGames"]}半庄',
            source_label,
            mode_label,
            f"Mortal {model_label}",
        ]
    )

    ai_label = "加权 AI 一致率" if data["aiWeighted"] else "平均 AI 一致率"
    if data["aiRate"] is None:
        ai_note = "当前数据未包含有效一致率"
    elif data["aiDenominator"]:
        ai_note = f'{data["aiDenominator"]} 次决策样本'
    else:
        ai_note = "按有值半庄简单平均"

    if data["badRate5"] is None and data["badRate10"] is None:
        bad_sample_note = "未采集；开启 analyze_bad_move_rate 后显示"
    elif data["badDenominator"]:
        bad_sample_note = f'{data["badDenominator"]} 次决策样本'
    else:
        bad_sample_note = "按有值半庄简单平均"

    ai_section = ""
    if data["aiRate"] is not None:
        ai_section = """
        <section class="chart-section">
            <div class="section-head">
                <div><h2>AI 一致率推移</h2></div>
                <div class="trend-controls" role="group" aria-label="趋势图数据密度">
                    <button type="button" class="trend-toggle is-active" data-trend-view="overview" aria-pressed="true">概览</button>
                    <button type="button" class="trend-toggle" data-trend-view="detail" aria-pressed="false">明细</button>
                </div>
            </div>
            <div id="ai-chart" class="chart" role="img" aria-label="AI 一致率推移"></div>
        </section>"""

    distribution_section = ""
    if data["histogram"]:
        distribution_section = """
        <section class="chart-section">
            <div class="section-head">
                <div><h2>Rating 分布</h2></div>
                <button type="button" id="rating-selection-clear" class="selection-clear" hidden>取消选择</button>
            </div>
            <div id="distribution-chart" class="chart" role="img" aria-label="Rating 分布；再次点击取消，按住 Ctrl 可多选"></div>
        </section>"""

    rendered = REPORT_TEMPLATE.substitute(
        page_title=html.escape(f"{nickname} · Mortal 分析报告"),
        nickname=html.escape(nickname),
        metadata=html.escape(metadata),
        rating_dense_range=(
            f'{data["ratingDenseLower"]:.1f}–{data["ratingDenseUpper"]:.1f}'
        ),
        rating_mean=_format_number(data["ratingMean"], 2),
        total_games=str(data["totalGames"]),
        rating_median=_format_number(data["ratingMedian"], 2),
        ai_label=ai_label,
        ai_rate=_format_number(data["aiRate"], 1, "%"),
        ai_note=html.escape(ai_note),
        bad_rate_5=_format_number(data["badRate5"], 1, "%"),
        bad_rate_10=_format_number(data["badRate10"], 1, "%"),
        bad_sample_note=html.escape(bad_sample_note),
        ai_section=ai_section,
        distribution_section=distribution_section,
        worst_rows=_worst_game_rows(data),
        date_end=html.escape(data["dateEnd"][:10] or "未知日期"),
        payload=_safe_json(data),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    return output_path


def save_png(html_path: str, png_path: str):
    from seleniumbase import SB

    abs_html = os.path.abspath(html_path)
    file_url = "file:///" + urllib.parse.quote(abs_html.replace("\\", "/"))

    with SB(uc=True, headless=True) as sb:
        sb.set_window_size(1500, 1100)
        sb.open(file_url)
        sb.wait_for_ready_state_complete()
        sb.wait_for_element_visible("#main")
        sb.sleep(1.0)
        report_height = sb.execute_script(
            "return Math.ceil(document.getElementById('main').getBoundingClientRect().height);"
        )
        sb.set_window_size(1500, min(16000, int(report_height) + 240))
        sb.sleep(0.5)
        sb.save_screenshot(png_path, selector="#main")


def plot_results(
    nickname: str,
    plot_mode: str,
    output_format: str = "xlsx",
    plot_limit: int | None = None,
    output_root: str | None = None,
):
    if plot_mode in ["none", None]:
        return

    safe_nick = _safe_nickname(nickname)
    if output_root is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_root = os.path.join(base_dir, "results", "majsoul", safe_nick)
    os.makedirs(output_root, exist_ok=True)

    html_path = os.path.join(output_root, f"report_{safe_nick}.html")
    png_path = os.path.join(output_root, f"report_{safe_nick}.png")

    logging.info(
        "Generating charts for %s (Mode: %s, Limit: %s)...",
        nickname,
        plot_mode,
        plot_limit or "all",
    )
    result = generate_html(
        nickname,
        html_path,
        output_format,
        plot_limit=plot_limit,
        results_root=output_root,
    )
    if not result:
        logging.warning("Skipping chart generation.")
        return

    if plot_mode in ["png", "both"]:
        try:
            save_png(html_path, png_path)
            logging.info("Saved PNG chart to: %s", png_path)
        except Exception as exc:
            logging.error("Failed to render PNG chart: %s", exc)

    if plot_mode in ["html", "both"]:
        logging.info("Saved HTML chart to: %s", html_path)
    elif plot_mode == "png":
        try:
            os.remove(html_path)
        except OSError:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Mortal Analysis Chart")
    parser.add_argument("nickname", help="Player nickname")
    parser.add_argument(
        "--plot-limit",
        "--plot_limit",
        type=int,
        default=None,
        help="Only use the latest N records for chart (default: all)",
        dest="plot_limit",
    )
    args = parser.parse_args()

    plot_results(args.nickname, "both", "xlsx", plot_limit=args.plot_limit)
