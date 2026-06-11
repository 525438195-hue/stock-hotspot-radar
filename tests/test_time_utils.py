from time_utils import format_publish_time


def test_format_publish_time_converts_common_formats_to_beijing_time() -> None:
    assert format_publish_time("Wed, 10 Jun 2026 04:13:54 GMT") == "2026-06-10 12:13"
    assert format_publish_time("2026-06-10T04:13:54Z") == "2026-06-10 12:13"
    assert format_publish_time("2026-06-10 04:13:54") == "2026-06-10 12:13"


def test_format_publish_time_hides_unparsed_english_dates() -> None:
    assert format_publish_time("bad Wed value") == "时间未知"
