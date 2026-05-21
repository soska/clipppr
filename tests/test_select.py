import pytest

from clipper.select import format_timestamp, parse_selection


def test_parse_all():
    assert parse_selection("all", 4) == [0, 1, 2, 3]


def test_parse_indices_and_ranges():
    assert parse_selection("1,3", 5) == [0, 2]
    assert parse_selection("1-3", 5) == [0, 1, 2]
    assert parse_selection("3-1", 5) == [0, 1, 2]  # reversed range
    assert parse_selection("1-2, 4", 5) == [0, 1, 3]


def test_parse_dedupes_preserving_order():
    assert parse_selection("3,1,3,1", 5) == [2, 0]


def test_parse_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_selection("9", 5)
    with pytest.raises(ValueError):
        parse_selection("0", 5)


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_selection("abc", 5)
    with pytest.raises(ValueError):
        parse_selection("", 5)


def test_format_timestamp():
    assert format_timestamp(5) == "0:05"
    assert format_timestamp(75) == "1:15"
    assert format_timestamp(3661) == "1:01:01"
