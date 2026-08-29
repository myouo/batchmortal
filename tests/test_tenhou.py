from datetime import datetime, timedelta

import pytest

from batchmortal.tenhou import (
    build_tenhou_paipu_urls,
    decode_tenhou_viewpoint,
    format_tenhou_timestamp,
    normalize_tenhou_modes,
    parse_tenhou_log_id,
    parse_tenhou_log_url,
    TENHOU_LOG_TIMEZONE,
    tenhou_mode_matches,
    tenhou_record_mode,
)


PLAYER = "ププリン"


def make_record(
    log_id: str,
    *,
    starttime: int,
    playernum: int = 4,
    playlength: int = 2,
    url_host: str = "tenhou.net",
) -> dict:
    record = {
        "starttime": starttime,
        "during": 32,
        "playernum": playernum,
        "playlength": playlength,
        "player1": PLAYER,
        "player2": "Tatsuno7",
        "player3": "Navitas",
        "tw": "198",
        "url": f"http://{url_host}/0/?log={log_id}",
    }
    if playernum == 4:
        record["player4"] = "tca00"
    return record


def test_decode_nodocchi_viewpoint_from_real_example():
    # Nodocchi returns 198 for this game. ププリン is player1 (first place),
    # and the site's two-bit permutation decoding resolves that to Tenhou seat 2.
    assert decode_tenhou_viewpoint("198", 1) == 2


def test_decode_tenhou_viewpoint_rejects_non_numeric_tw():
    assert decode_tenhou_viewpoint("abc", 1) is None
    assert decode_tenhou_viewpoint(None, 1) is None


@pytest.mark.parametrize("player_order", [0, 5, None])
def test_decode_tenhou_viewpoint_rejects_invalid_player_order(player_order):
    assert decode_tenhou_viewpoint("198", player_order) is None


def test_decode_tenhou_viewpoint_returns_none_for_unmapped_packed_value():
    assert decode_tenhou_viewpoint("999", 1) is None


def test_build_real_example_paipu_url():
    record = make_record(
        "2026071212gm-00a9-0000-2dab9d24",
        starttime=1783828560,
    )

    assert build_tenhou_paipu_urls([record], PLAYER) == [
        {
            "source": "tenhou",
            "mode": "4p-south",
            "uuid": "2026071212gm-00a9-0000-2dab9d24",
            "paipuUrl": "http://tenhou.net/0/?log=2026071212gm-00a9-0000-2dab9d24&tw=2",
            "startTime": format_tenhou_timestamp(1783828560),
            "endTime": format_tenhou_timestamp(1783828560 + 32 * 60),
        }
    ]


def test_build_tenhou_paipu_urls_deduplicates_log_ids():
    log_id = "2026071212gm-00a9-0000-11111111"
    records = [
        make_record(log_id, starttime=200),
        make_record(log_id, starttime=100),
    ]

    items = build_tenhou_paipu_urls(records, PLAYER)

    assert len(items) == 1
    assert items[0]["uuid"] == log_id
    assert items[0]["startTime"] == format_tenhou_timestamp(200)


def test_build_tenhou_paipu_urls_skips_invalid_viewpoints():
    valid_log_id = "2026071212gm-00a9-0000-11111111"
    invalid_tw = make_record(
        "2026071212gm-00a9-0000-22222222",
        starttime=200,
    )
    invalid_tw["tw"] = "999"
    missing_player = make_record(
        "2026071212gm-00a9-0000-33333333",
        starttime=100,
    )
    missing_player["player1"] = "another-player"

    items = build_tenhou_paipu_urls(
        [make_record(valid_log_id, starttime=300), invalid_tw, missing_player],
        PLAYER,
    )

    assert [item["uuid"] for item in items] == [valid_log_id]


def test_mode_filter_and_limit_are_applied_per_actual_mode():
    records = [
        make_record("2026071213gm-00a9-0000-aaaaaaaa", starttime=300, playlength=2),
        make_record("2026071212gm-00a9-0000-bbbbbbbb", starttime=200, playlength=2),
        make_record("2026071211gm-00a9-0000-cccccccc", starttime=100, playlength=1),
    ]

    items = build_tenhou_paipu_urls(records, PLAYER, modes="4p", limit=1)

    assert [item["uuid"] for item in items] == [
        "2026071213gm-00a9-0000-aaaaaaaa",
        "2026071211gm-00a9-0000-cccccccc",
    ]
    assert [item["mode"] for item in items] == ["4p-south", "4p-east"]


def test_non_tenhou_urls_are_not_mistaken_for_tenhou_records():
    record = make_record(
        "2026071212gm-00a9-0000-2dab9d24",
        starttime=1783828560,
        url_host="game.maj-soul.com",
    )

    assert build_tenhou_paipu_urls([record], PLAYER) == []
    assert parse_tenhou_log_url(record["url"]) is None


@pytest.mark.parametrize(
    ("log_id", "expected_mode"),
    [
        ("2019050417gm-0029-0000-4f2a8622", "4p-south"),
        ("2013022000gm-00b9-0000-73f9f276", "3p-south"),
        ("2013022000gm-00e1-0000-b946ce74", "4p-east"),
    ],
)
def test_parse_tenhou_log_id_decodes_hour_and_rule_bits(log_id, expected_mode):
    metadata = parse_tenhou_log_id(log_id)

    assert metadata is not None
    expected_start = (
        datetime.strptime(log_id[:10], "%Y%m%d%H")
        .replace(tzinfo=TENHOU_LOG_TIMEZONE)
        .astimezone()
        .strftime("%Y-%m-%d %H:00:00")
    )
    assert metadata["start_time"] == expected_start
    assert metadata["mode"] == expected_mode


def test_parse_tenhou_log_id_rejects_invalid_calendar_values():
    assert parse_tenhou_log_id("2019133217gm-0029-0000-4f2a8622") is None


def test_log_id_hour_uses_the_same_local_timezone_as_nodocchi_timestamps():
    metadata = parse_tenhou_log_id("2026071212gm-00a9-0000-2dab9d24")

    assert metadata is not None
    approximate = datetime.strptime(metadata["start_time"], "%Y-%m-%d %H:%M:%S")
    actual = datetime.strptime(
        format_tenhou_timestamp(1783828560),
        "%Y-%m-%d %H:%M:%S",
    )
    assert timedelta(0) <= actual - approximate < timedelta(hours=1)


def test_records_without_url_or_viewpoint_are_skipped():
    no_url = make_record("2026071212gm-00a9-0000-2dab9d24", starttime=200)
    no_url.pop("url")
    no_viewpoint = make_record("2026071213gm-00a9-0000-aaaaaaaa", starttime=300)
    no_viewpoint.pop("tw")

    assert build_tenhou_paipu_urls([no_url, no_viewpoint], PLAYER) == []


def test_tenhou_mode_aliases_and_labels():
    assert normalize_tenhou_modes("四南,3p-east") == ("4p-south", "3p-east")
    assert normalize_tenhou_modes("*") == ("all",)
    assert tenhou_record_mode({"playernum": 3, "playlength": 1}) == "3p-east"


def test_normalize_tenhou_modes_rejects_unsupported_label():
    with pytest.raises(ValueError, match="Unsupported Tenhou mode 'foo'"):
        normalize_tenhou_modes(("foo",))


@pytest.mark.parametrize("modes", ["", (), ("",), ("   ",), ("  ", "", "   ")])
def test_normalize_tenhou_modes_empty_values_fall_back_to_all(modes):
    assert normalize_tenhou_modes(modes) == ("all",)


def test_tenhou_mode_matches_normalized_modes():
    four_player = normalize_tenhou_modes(("4p",))
    assert tenhou_mode_matches("4p-east", four_player)
    assert tenhou_mode_matches("4p-south", four_player)
    assert not tenhou_mode_matches("3p-east", four_player)
    assert not tenhou_mode_matches("3p-south", four_player)

    east_only = normalize_tenhou_modes(("4p-east",))
    assert tenhou_mode_matches("4p-east", east_only)
    assert not tenhou_mode_matches("4p-south", east_only)

    south_only = normalize_tenhou_modes(("4p-south",))
    assert tenhou_mode_matches("4p-south", south_only)
    assert not tenhou_mode_matches("4p-east", south_only)
