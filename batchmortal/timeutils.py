from datetime import datetime, timezone


UTC_RESULT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_utc_datetime(value: datetime) -> str:
    """Format an aware datetime as the canonical result timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Result datetimes must include timezone information.")
    return value.astimezone(timezone.utc).strftime(UTC_RESULT_FORMAT)


def format_unix_timestamp(value) -> str:
    """Format a Unix timestamp as canonical UTC, returning empty for invalid values."""
    try:
        timestamp = float(value)
        if timestamp <= 0:
            return ""
        return format_utc_datetime(datetime.fromtimestamp(timestamp, timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def utc_now_string() -> str:
    return format_utc_datetime(datetime.now(timezone.utc))
