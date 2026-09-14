from datetime import datetime, timedelta, timezone

import pytest

from batchmortal.timeutils import format_unix_timestamp, format_utc_datetime


def test_result_datetime_is_always_explicit_utc():
    source = datetime(
        2026,
        8,
        29,
        20,
        30,
        tzinfo=timezone(timedelta(hours=8)),
    )

    assert format_utc_datetime(source) == "2026-08-29T12:30:00Z"
    assert format_unix_timestamp(1788004043) == "2026-08-29T11:47:23Z"


def test_result_datetime_rejects_ambiguous_naive_values():
    with pytest.raises(ValueError, match="timezone information"):
        format_utc_datetime(datetime(2026, 8, 29, 12, 30))
