import pytest

from batchmortal.api import (
    acc2match,
    format_majsoul_uuid_date,
    parse_majsoul_paipu_url,
)


UUID = "260829-e70ff6e7-e2fa-4545-9022-a550b4dbc42f"


def test_acc2match():
    # From decode.py and JS selfTest: acc2match(15628582) should equal 63606719
    expected = 63606719
    got = acc2match(15628582)
    if got == expected:
        print(f"[Test] acc2match(15628582) = {got} ✓")
    else:
        print(f"[Test] acc2match MISMATCH: expected {expected}, got {got}")
        exit(1)


@pytest.mark.parametrize(
    "url",
    [
        f"https://game.maj-soul.com/1/?paipu={UUID}_a12345678",
        f"https://game.maj-soul.com/1/?paipu={UUID}_a12345678_2",
        f"https://mahjongsoul.game.yo-star.com/?paipu={UUID}_a12345678",
    ],
)
def test_parse_majsoul_paipu_url_supports_official_shared_link_hosts(url):
    parsed = parse_majsoul_paipu_url(url)

    assert parsed is not None
    assert parsed[0] == UUID
    assert parsed[1] == url


def test_parse_majsoul_paipu_url_rejects_embedded_or_untrusted_links():
    trusted = f"https://game.maj-soul.com/1/?paipu={UUID}_a12345678"

    assert parse_majsoul_paipu_url(f"prefix {trusted}") is None
    assert parse_majsoul_paipu_url(
        f"https://example.com/1/?paipu={UUID}_a12345678"
    ) is None


def test_format_majsoul_uuid_date_uses_only_valid_encoded_dates():
    assert format_majsoul_uuid_date(UUID) == "2026-08-29"
    assert format_majsoul_uuid_date(UUID.replace("260829", "261332")) == ""


if __name__ == '__main__':
    test_acc2match()
