import requests
import urllib.parse
import logging
import time
from datetime import datetime, timezone

BASE_URL = 'https://5-data.amae-koromo.com/api/v2/pl4'
OFFSET_2 = [1117113, 1358437]
XOR_CODE_2 = 86216345
REQUEST_HEADERS = {"Accept": "application/json"}
SESSION = requests.Session()

def acc2match(account_id: int) -> int:
    """
    Convert an account_id into the match_id embedded at the end of a Mahjong Soul paipu URL.
    """
    return ((7 * account_id + OFFSET_2[0]) ^ XOR_CODE_2) + OFFSET_2[1]

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
