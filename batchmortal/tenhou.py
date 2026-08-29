import logging
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests


NODOCCHI_API_URL = "https://nodocchi.moe/api/listuser.php"
REQUEST_HEADERS = {"Accept": "application/json"}
SESSION = requests.Session()

TENHOU_MODE_ALIASES = {
    "all": "all",
    "*": "all",
    "4": "4p",
    "4p": "4p",
    "yonma": "4p",
    "四麻": "4p",
    "4e": "4p-east",
    "4p-east": "4p-east",
    "四东": "4p-east",
    "四東": "4p-east",
    "4s": "4p-south",
    "4p-south": "4p-south",
    "四南": "4p-south",
    "3": "3p",
    "3p": "3p",
    "sanma": "3p",
    "三麻": "3p",
    "3e": "3p-east",
    "3p-east": "3p-east",
    "三东": "3p-east",
    "三東": "3p-east",
    "3s": "3p-south",
    "3p-south": "3p-south",
    "三南": "3p-south",
}
TENHOU_MODE_CHOICES = (
    "all",
    "4p",
    "4p-east",
    "4p-south",
    "3p",
    "3p-east",
    "3p-south",
)
TENHOU_LOG_ID_RE = re.compile(
    r"^(?P<hour>\d{10})gm-(?P<game_type>[0-9a-f]{4})-[0-9a-z]+-[0-9a-z]{8}$",
    re.IGNORECASE,
)
TENHOU_GAME_TYPE_HANCHAN = 0x08
TENHOU_GAME_TYPE_SANMA = 0x10
TENHOU_LOG_TIMEZONE = timezone(timedelta(hours=9))


def fetch_tenhou_player_records(player_name: str) -> tuple[str, list[dict]]:
    """Fetch a Tenhou player's records from nodocchi.moe."""
    try:
        response = SESSION.get(
            NODOCCHI_API_URL,
            params={"name": player_name},
            timeout=30,
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"Nodocchi API request failed while searching for '{player_name}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Unexpected Nodocchi API response: expected an object.")

    retry_after = data.get("retry")
    if retry_after:
        raise RuntimeError(
            f"Nodocchi API asked the client to retry after {retry_after} seconds."
        )

    records = data.get("list")
    if not isinstance(records, list):
        raise ValueError("Unexpected Nodocchi API response: missing 'list' field.")
    if not records:
        raise ValueError(f"Tenhou player not found or has no records: '{player_name}'.")

    resolved_name = data.get("name")
    if not isinstance(resolved_name, str) or not resolved_name:
        resolved_name = player_name

    logging.info(
        "[Nodocchi] Found Tenhou player '%s' with %d records.",
        resolved_name,
        len(records),
    )
    return resolved_name, records


def normalize_tenhou_modes(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_modes = value.split(",")
    else:
        raw_modes = value

    normalized = []
    for raw_mode in raw_modes:
        key = str(raw_mode).strip().lower().replace("_", "-")
        if not key:
            continue
        mode = TENHOU_MODE_ALIASES.get(key)
        if not mode:
            supported = ", ".join(TENHOU_MODE_CHOICES)
            raise ValueError(
                f"Unsupported Tenhou mode '{raw_mode}'. Supported values: {supported}"
            )
        if mode not in normalized:
            normalized.append(mode)

    if not normalized:
        return ("all",)
    if "all" in normalized:
        return ("all",)
    return tuple(normalized)


def tenhou_record_mode(record: dict) -> str:
    try:
        player_num = int(record.get("playernum"))
        play_length = int(record.get("playlength"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Tenhou record is missing a valid playernum/playlength.") from exc

    if player_num not in (3, 4) or play_length not in (1, 2):
        raise ValueError(
            f"Unsupported Tenhou record mode: playernum={player_num}, playlength={play_length}."
        )

    length_name = {1: "east", 2: "south"}[play_length]
    return f"{player_num}p-{length_name}"


def tenhou_mode_matches(record_mode: str, selected_modes: tuple[str, ...]) -> bool:
    if "all" in selected_modes:
        return True
    return any(
        record_mode == selected_mode
        or (selected_mode in ("3p", "4p") and record_mode.startswith(f"{selected_mode}-"))
        for selected_mode in selected_modes
    )


def find_player_order(record: dict, player_name: str) -> int | None:
    try:
        player_num = int(record.get("playernum"))
    except (TypeError, ValueError):
        return None

    for order in range(1, player_num + 1):
        if record.get(f"player{order}") == player_name:
            return order
    return None


def decode_tenhou_viewpoint(encoded_tw, player_order: int | None) -> int | None:
    """
    Decode Nodocchi's packed seat permutation.

    This mirrors the site's frontend: each two-bit group stores the result
    order for one Tenhou seat. The seat whose value equals order - 1 is the
    requested player's ``tw`` value.
    """
    try:
        packed = int(encoded_tw)
    except (TypeError, ValueError):
        return None

    if player_order not in (1, 2, 3, 4):
        return None

    expected_order = player_order - 1
    for seat in range(4):
        if (packed >> (seat * 2)) & 3 == expected_order:
            return seat
    return None


def parse_tenhou_log_url(url: str) -> tuple[str, str] | None:
    """Return ``(log_id, canonical_base_url)`` for an official Tenhou log URL."""
    if not isinstance(url, str) or not url:
        return None

    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if host not in ("tenhou.net", "www.tenhou.net"):
        return None
    if parsed.path not in ("/0", "/0/"):
        return None

    log_values = urllib.parse.parse_qs(parsed.query).get("log", [])
    if len(log_values) != 1 or not TENHOU_LOG_ID_RE.fullmatch(log_values[0]):
        return None

    log_id = log_values[0]
    canonical_url = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            host,
            "/0/",
            urllib.parse.urlencode({"log": log_id}),
            "",
        )
    )
    return log_id, canonical_url


def parse_tenhou_log_id(log_id: str) -> dict | None:
    """Decode the timestamp precision and game mode carried by a Tenhou log ID."""
    match = TENHOU_LOG_ID_RE.fullmatch(str(log_id))
    if not match:
        return None

    try:
        started_at = (
            datetime.strptime(match.group("hour"), "%Y%m%d%H")
            .replace(tzinfo=TENHOU_LOG_TIMEZONE)
            .astimezone()
        )
        game_type = int(match.group("game_type"), 16)
    except ValueError:
        return None

    player_count = 3 if game_type & TENHOU_GAME_TYPE_SANMA else 4
    game_length = "south" if game_type & TENHOU_GAME_TYPE_HANCHAN else "east"
    return {
        "start_time": started_at.strftime("%Y-%m-%d %H:00:00"),
        "mode": f"{player_count}p-{game_length}",
    }


def format_tenhou_timestamp(timestamp) -> str:
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def build_tenhou_paipu_urls(
    records: list[dict],
    player_name: str,
    modes: str | list[str] | tuple[str, ...] = "all",
    limit: int | None = None,
) -> list[dict]:
    """Build recent, player-focused Tenhou paipu URLs from Nodocchi records."""
    selected_modes = normalize_tenhou_modes(modes)
    per_mode_count: defaultdict[str, int] = defaultdict(int)
    results = []
    seen_log_ids = set()
    skipped_without_url = 0
    skipped_invalid = 0

    def start_time(record):
        try:
            return int(record.get("starttime", 0))
        except (TypeError, ValueError):
            return 0

    for record in sorted(records, key=start_time, reverse=True):
        try:
            mode = tenhou_record_mode(record)
        except ValueError:
            skipped_invalid += 1
            continue
        if not tenhou_mode_matches(mode, selected_modes):
            continue
        if limit is not None and per_mode_count[mode] >= limit:
            continue

        raw_url = record.get("url")
        if not raw_url:
            skipped_without_url += 1
            continue
        parsed_url = parse_tenhou_log_url(raw_url)
        if not parsed_url:
            skipped_invalid += 1
            continue

        player_order = find_player_order(record, player_name)
        viewpoint = decode_tenhou_viewpoint(record.get("tw"), player_order)
        if viewpoint is None:
            skipped_invalid += 1
            continue

        log_id, base_url = parsed_url
        if log_id in seen_log_ids:
            continue

        start_timestamp = start_time(record)
        try:
            duration_seconds = max(0, int(record.get("during", 0))) * 60
        except (TypeError, ValueError):
            duration_seconds = 0

        results.append(
            {
                "source": "tenhou",
                "mode": mode,
                "uuid": log_id,
                "paipuUrl": f"{base_url}&tw={viewpoint}",
                "startTime": format_tenhou_timestamp(start_timestamp),
                "endTime": format_tenhou_timestamp(start_timestamp + duration_seconds),
            }
        )
        seen_log_ids.add(log_id)
        per_mode_count[mode] += 1

    logging.info(
        "[Nodocchi] Built %d Tenhou links (unavailable=%d, invalid=%d).",
        len(results),
        skipped_without_url,
        skipped_invalid,
    )
    return results
