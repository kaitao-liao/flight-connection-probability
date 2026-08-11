import pytest
from backend.flight_connection.acquire import hhmm_to_minutes, time_bucket


def test_hhmm_conversion_and_midnight():
    assert hhmm_to_minutes("5") == 5
    assert hhmm_to_minutes("1230.00") == 750
    assert hhmm_to_minutes("2400") == 0


def test_invalid_hhmm():
    with pytest.raises(ValueError):
        hhmm_to_minutes("1260")


def test_time_bucket_boundaries():
    assert [time_bucket(x) for x in (0, 360, 720, 1080)] == ["overnight", "morning", "afternoon", "evening"]

