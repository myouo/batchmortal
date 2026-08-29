import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone

import requests

BASE_URL = 'https://5-data.amae-koromo.com/api/v2/pl4'
OFFSET_2 = [1117113, 1358437]
XOR_CODE_2 = 86216345
REQUEST_HEADERS = {"Accept": "application/json"}
SESSION = requests.Session()
MAJSOUL_PAIPU_HOST_PATHS = {
    "game.maj-soul.com": {"/1", "/1/"},
    "mahjongsoul.game.yo-star.com": {"", "/"},
}
MAJSOUL_UUID_PATTERN = (
    r"\d{6}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
MAJSOUL_UUID_RE = re.compile(
    rf"^{MAJSOUL_UUID_PATTERN}$",
    re.IGNORECASE,
)
MAJSOUL_PAIPU_VALUE_RE = re.compile(
    rf"(?P<uuid>{MAJSOUL_UUID_PATTERN})_a\d+(?:_\d+)?",
    re.IGNORECASE,
)


def acc2match(account_id: int) -> int:
    """
    Convert an account_id into the match_id embedded at the end of a Mahjong Soul paipu URL.
    """
    return ((7 * account_id + OFFSET_2[0]) ^ XOR_CODE_2) + OFFSET_2[1]


def parse_majsoul_paipu_url(url: str) -> tuple[str, str] | None:
    """Return ``(uuid, canonical_url)`` for a supported Mahjong Soul log URL."""
    if not isinstance(url, str) or not url:
        return None

    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None

    if parsed.path not in MAJSOUL_PAIPU_HOST_PATHS.get(host, set()):
        return None

    paipu_values = urllib.parse.parse_qs(parsed.query).get("paipu", [])
    if len(paipu_values) != 1:
        return None
    match = MAJSOUL_PAIPU_VALUE_RE.fullmatch(paipu_values[0])
    if not match:
        return None

    uuid = match.group("uuid")
    canonical_url = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            host,
            parsed.path or "/",
            urllib.parse.urlencode({"paipu": paipu_values[0]}),
            "",
        )
    )
    return uuid, canonical_url


def format_majsoul_uuid_date(uuid: str) -> str:
    """Return the calendar date encoded by a Mahjong Soul game UUID."""
    match = MAJSOUL_UUID_RE.fullmatch(str(uuid))
    if not match:
        return ""

    value = str(uuid)[:6]
    try:
        parsed = datetime(
            2000 + int(value[:2]),
            int(value[2:4]),
            int(value[4:6]),
        )
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%d")


def search_player(nickname: str) -> int:
    """
    Search for a player by nickname and return their account_id.
    """
    url = f"{BASE_URL}/search_player/{urllib.parse.quote(nickname)}?limit=20&tag=all"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        raise RuntimeError(f"API request failed while searching for '{nickname}': {e}")
    
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"Player not found: '{nickname}'. The search returned an empty result.")
        
    player = data[0]
    if "id" not in player:
        raise ValueError("Unexpected API response structure: missing 'id' field.")
        
    logging.info(f"[API] Found player: '{player['nickname']}' (account_id={player['id']})")
    return player["id"]

def get_player_nickname_by_id(account_id: int) -> str | None:
    """
    Fetch a player's nickname using their account_id.
    """
    end_ms = int(time.time() * 1000)
    start_ms = 1262304000000
    
    url = f"{BASE_URL}/player_stats/{account_id}/{start_ms}/{end_ms}?mode=16.12.9.15.11.8"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        data = res.json()
        return data.get("nickname")
    except Exception as e:
        logging.warning(f"[API] Failed to fetch nickname for account_id={account_id}: {e}")
        return None

def get_player_records(account_id: int, limit: int, mode: int) -> list:
    """
    Fetch a player's recent game records for the given mode.
    """
    end_ms = int(time.time() * 1000)
    start_ms = 1262304000000
    
    url = f"{BASE_URL}/player_records/{account_id}/{end_ms}/{start_ms}?limit={limit}&mode={mode}&descending=true"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        raise RuntimeError(f"API request failed while fetching records (mode={mode}): {e}")
        
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for player_records: not a list.")
        
    logging.info(f"[API] Fetched {len(data)} records for mode={mode}")
    return data

def format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    if ts > 1e11:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def build_paipu_urls(records: list, account_id: int) -> list:
    """
    Build a list of paipu URLs from game records.
    """
    match_id = 'a' + str(acc2match(account_id))
    results = []
    for rec in records:
        uuid_str = rec.get("uuid")
        if not uuid_str:
            continue
            
        start_time = format_timestamp(rec.get("startTime", 0))
        end_time = format_timestamp(rec.get("endTime", 0))

        results.append({
            "uuid": uuid_str,
            "matchId": match_id,
            "paipuUrl": f"https://game.maj-soul.com/1/?paipu={uuid_str}_{match_id}",
            "startTime": start_time,
            "endTime": end_time
        })
    return results
