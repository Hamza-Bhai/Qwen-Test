"""Fair competitor benchmark maths (pure functions, fixed dates)."""

from datetime import datetime, timedelta, timezone

from app.studio import benchmarks as b
from app.studio.benchmarks import VideoStat

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def vs(i, days_old, views, *, short=False, title="Sunday reset", tags=None, hashtags=None, published=True):
    """A video aged exactly ``days_old`` days at NOW."""
    return VideoStat(
        youtube_video_id=f"id{i:09d}",
        title=title,
        published_at=NOW - timedelta(days=days_old) if published else None,
        view_count=views,
        is_short=short,
        tags=tags or [],
        hashtags=hashtags or [],
    )


def mature_long(n, vpd=100):
    """n long videos, 10 days old, each with ``vpd`` views/day."""
    return [vs(i, 10, vpd * 10) for i in range(n)]


def test_age_days_none_for_missing_or_future():
    assert b.age_days(None, NOW) is None
    assert b.age_days(NOW + timedelta(hours=1), NOW) is None
    assert b.age_days(NOW - timedelta(days=2), NOW) == 2.0


def test_age_days_treats_naive_datetimes_as_utc():
    naive = (NOW - timedelta(days=3)).replace(tzinfo=None)  # what SQLite hands back
    assert b.age_days(naive, NOW) == 3.0


def test_views_per_day_excludes_under_one_day():
    assert b.views_per_day(vs(1, 0.5, 1000), NOW) is None
    assert b.views_per_day(vs(2, 1, 1000), NOW) == 1000.0
    assert b.views_per_day(vs(3, 10, 500, published=False), NOW) is None


def test_format_key():
    assert b.format_key(vs(1, 10, 1, short=True)) == "short"
    assert b.format_key(vs(2, 10, 1, short=False)) == "long"
    unknown = VideoStat("x", "t", NOW, 1, None, [], [])
    assert b.format_key(unknown) is None


def test_median_vpd_uses_only_mature_same_format():
    videos = [
        vs(1, 10, 1000),            # long, vpd 100
        vs(2, 20, 6000),            # long, vpd 300
        vs(3, 10, 5000),            # long, vpd 500
        vs(4, 3, 99999),            # too young -> ignored
        vs(5, 10, 777000, short=True),  # other format -> ignored
    ]
    assert b.median_vpd(videos, NOW, fmt="long") == 300.0
    assert b.median_vpd(videos, NOW, fmt="short") == 77700.0
    assert b.median_vpd([vs(9, 2, 10)], NOW, fmt="long") is None


def test_breakout_detected_at_3x_and_not_below():
    base = mature_long(11)  # 11 x vpd 100
    result = b.find_breakouts(base + [vs(100, 10, 3000)], NOW)  # exactly 3.0x the median
    assert result.status == "ok"
    assert [x.youtube_video_id for x in result.breakouts] == [f"id{100:09d}"]
    assert result.breakouts[0].multiple == 3.0 and result.breakouts[0].format == "long"
    assert result.breakouts[0].channel_median_vpd == 100.0

    just_below = b.find_breakouts(base + [vs(101, 10, 2900)], NOW)  # 2.9x
    assert just_below.status == "ok" and just_below.breakouts == []


def test_breakouts_sorted_by_multiple_descending():
    videos = mature_long(10) + [vs(100, 10, 4000), vs(101, 10, 9000)]
    result = b.find_breakouts(videos, NOW)
    assert [x.multiple for x in result.breakouts] == [9.0, 4.0]


def test_breakouts_inconclusive_below_10_comparable():
    result = b.find_breakouts(mature_long(9), NOW)
    assert result.status == "inconclusive" and result.breakouts == []
    assert "9" in result.note and "10" in result.note


def test_young_videos_do_not_count_as_comparable():
    result = b.find_breakouts(mature_long(9) + [vs(50, 3, 90000)], NOW)
    assert result.status == "inconclusive"


def test_breakouts_evaluate_each_format_separately():
    videos = mature_long(11) + [vs(100, 10, 3000)]
    videos += [vs(200 + i, 10, 100, short=True) for i in range(4)] + [vs(300, 10, 9000, short=True)]
    result = b.find_breakouts(videos, NOW)
    assert result.status == "ok"
    assert {x.format for x in result.breakouts} == {"long"}  # shorts: only 5 comparable -> not evaluated


def test_zero_median_gives_no_breakouts_instead_of_dividing_by_zero():
    videos = [vs(i, 10, 0) for i in range(10)] + [vs(99, 10, 5000)]
    result = b.find_breakouts(videos, NOW)
    assert result.breakouts == []


def test_early_breakouts_need_5x_and_age_1_to_7_days():
    base = mature_long(10)
    fast = vs(100, 2, 1200)      # 600/day = 6x
    slow = vs(101, 2, 800)       # 400/day = 4x
    too_new = vs(102, 0.5, 99999)
    too_old = vs(103, 8, 8000)   # mature, not "early"
    got = b.early_breakouts(base + [fast, slow, too_new, too_old], NOW)
    assert [x.youtube_video_id for x in got] == [fast.youtube_video_id]
    assert got[0].multiple == 6.0


def test_early_breakouts_empty_without_10_mature_comparable():
    assert b.early_breakouts(mature_long(9) + [vs(100, 2, 9999)], NOW) == []


def test_upload_frequency_unreliable_when_short_span_or_few_uploads():
    few = [vs(i, d, 1) for i, d in enumerate([1, 10, 20])]
    assert b.upload_frequency(few, NOW).reliable is False
    short_span = [vs(i, d, 1) for i, d in enumerate([1, 3, 5, 7, 9])]
    freq = b.upload_frequency(short_span, NOW)
    assert freq.reliable is False and freq.per_30_days is None and freq.uploads == 5


def test_upload_frequency_uses_n_minus_1():
    videos = [vs(i, d, 1) for i, d in enumerate([0, 10, 20, 30, 40, 60])]  # 6 uploads over 60 days
    freq = b.upload_frequency(videos, NOW)
    assert freq.reliable is True and freq.span_days == 60.0
    assert freq.per_30_days == 2.5  # (6 - 1) / 60 * 30


def test_upload_frequency_ignores_undated_and_future():
    videos = [vs(1, 5, 1), vs(2, 9, 1, published=False)]
    assert b.upload_frequency(videos, NOW).uploads == 1


def test_format_split_counts_unclassified():
    videos = [vs(1, 5, 1, short=True), vs(2, 5, 1), vs(3, 5, 1), VideoStat("u", "t", NOW, 1, None, [], [])]
    assert b.format_split(videos) == {"short": 1, "long": 2, "unclassified": 1}


def test_title_patterns_none_for_empty():
    assert b.title_patterns([]) is None


def test_title_patterns_counts_numbers_and_questions():
    videos = [
        vs(1, 5, 1, title="5 habits"),
        vs(2, 5, 1, title="Why?"),
        vs(3, 5, 1, title="Sunday reset"),
        vs(4, 5, 1, title="Reset your mind"),
    ]
    p = b.title_patterns(videos)
    assert p.n == 4
    assert p.pct_with_number == 25.0 and p.pct_question == 25.0
    assert p.avg_length == (len("5 habits") + len("Why?") + len("Sunday reset") + len("Reset your mind")) / 4
    assert ("reset", 2) in p.top_keywords and p.top_keywords[0] == ("reset", 2)


def test_recurring_counts_once_per_video_and_applies_min():
    groups = [["Calm", "calm", "focus"], ["calm"], ["Calm", "rare"], ["focus"], ["focus"]]
    assert b.recurring(groups, min_videos=3) == [("calm", 3), ("focus", 3)]
    assert b.recurring(groups, min_videos=4) == []


def test_breakout_publish_times_reports_sample_size():
    mk = lambda i, dt: b.Breakout(f"v{i}", "t", "long", 10, 1.0, 1.0, 3.0, dt)  # noqa: E731
    monday = datetime(2026, 9, 28, 9, tzinfo=timezone.utc)      # a Monday 09:00 UTC
    tuesday = datetime(2026, 9, 29, 9, tzinfo=timezone.utc)
    res = b.breakout_publish_times([mk(1, monday), mk(2, tuesday), mk(3, monday), mk(4, None)])
    assert res["sample"] == 3
    assert res["weekday_utc"][0] == 2 and res["weekday_utc"][1] == 1 and res["weekday_utc"][2] == 0
    assert res["hour_utc"][9] == 3 and len(res["hour_utc"]) == 24 and len(res["weekday_utc"]) == 7


def test_compare_with_me_handles_none_subscribers():
    owner = mature_long(6, vpd=50)
    comps = [(mature_long(6, vpd=100), None), (mature_long(6, vpd=300), 2000)]
    out = b.compare_with_me(owner, None, comps, NOW)
    long = out["long"]
    assert long.owner_median_vpd == 50.0 and long.owner_vpd_per_1k_subs is None
    assert long.watchlist_median_vpd == 200.0 and long.competitors_counted == 2
    assert long.watchlist_vpd_per_1k_subs == 150.0  # only the competitor with known subscribers: 300 / 2000 * 1000
    assert out["short"].owner_median_vpd is None and out["short"].competitors_counted == 0


def test_compare_with_me_per_1k_subscribers():
    out = b.compare_with_me(mature_long(5, vpd=40), 1000, [(mature_long(5, vpd=80), 4000)], NOW)
    assert out["long"].owner_vpd_per_1k_subs == 40.0
    assert out["long"].watchlist_vpd_per_1k_subs == 20.0


def test_compare_requires_5_mature_videos():
    out = b.compare_with_me(mature_long(4, vpd=50), 1000, [(mature_long(4, vpd=80), 1000)], NOW)
    assert out["long"].owner_median_vpd is None and out["long"].owner_videos == 4
    assert out["long"].watchlist_median_vpd is None and out["long"].competitors_counted == 0
