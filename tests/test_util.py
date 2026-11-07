from datetime import datetime

from app import util


def test_clean_header_strips_control_chars():
    assert util.clean_header("Hi\r\nBcc: evil@x.com") == "HiBcc: evil@x.com"


def test_is_valid_email():
    assert util.is_valid_email("a.b+c@sub.domain.co")
    assert not util.is_valid_email("no-at-sign")
    assert not util.is_valid_email("a@b")          # no dot / no TLD
    assert not util.is_valid_email("a@b.c\nBcc: x@y.com")
    assert not util.is_valid_email("")
    assert not util.is_valid_email(None)


def test_validate_recipients_splits_and_flags():
    valid, invalid = util.validate_recipients("a@x.com, bad, c@y.com; d@z.com")
    assert valid == ["a@x.com", "c@y.com", "d@z.com"]
    assert invalid == ["bad"]


def test_parse_send_window():
    assert util.parse_send_window("09:00-18:00") == (540, 1080)
    assert util.parse_send_window("") is None
    assert util.parse_send_window("nonsense") is None
    assert util.parse_send_window("25:00-26:00") is None


def test_in_send_window_normal():
    assert util.in_send_window("09:00-18:00", datetime(2026, 1, 1, 12, 0))
    assert not util.in_send_window("09:00-18:00", datetime(2026, 1, 1, 8, 0))


def test_in_send_window_wraps_midnight():
    assert util.in_send_window("22:00-06:00", datetime(2026, 1, 1, 23, 0))
    assert util.in_send_window("22:00-06:00", datetime(2026, 1, 1, 5, 0))
    assert not util.in_send_window("22:00-06:00", datetime(2026, 1, 1, 12, 0))


def test_empty_window_is_always_open():
    assert util.in_send_window("", datetime(2026, 1, 1, 3, 0))
