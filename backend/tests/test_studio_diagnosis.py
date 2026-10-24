"""Own-channel diagnosis rules and progress maths (pure functions, fixed fixtures)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.studio.diagnosis import (
    DayRow,
    ReachRow,
    TrafficRow,
    VideoRow,
    compute_progress,
    diagnose,
)

TODAY = date(2026, 10, 24)
CODES = ["low_ctr", "ctr_trend", "early_dropoff", "view_pct_by_format", "traffic_mix",
         "sub_conversion", "cadence", "title_keywords", "publish_timing"]


def d(n):  # n days before today
    return TODAY - timedelta(days=n)


def days(n_days, views=100, *, start=1, pct=40.0, gained=2, lost=1, minutes=None):
    return [DayRow(d(i), views, minutes if minutes is not None else views * 2, pct, gained, lost)
            for i in range(start, start + n_days)]


def video(vid, *, age=30, short=False, views=1000, pct=40.0, subs=5, title=None, published=None):
    published = published or datetime.combine(d(age), datetime.min.time(), tzinfo=timezone.utc)
    return VideoRow(vid, title or f"video {vid}", published, short, views, pct, subs)


def finding(findings, code):
    return next(f for f in findings if f.code == code)


def run(**kw):
    args = {"days": [], "videos": [], "traffic": [], "reach": [], "retention": {}}
    args.update(kw)
    return diagnose(args["days"], args["videos"], args["traffic"], args["reach"], args["retention"], TODAY)


# --- progress -------------------------------------------------------------------------------

def test_progress_changes_and_none_windows():
    rows = days(7, views=200, start=1) + days(7, views=100, start=8)
    p = compute_progress(rows, [], TODAY)
    assert p["views"]["last_7"] == 1400 and p["views"]["prev_7"] == 700
    assert p["views"]["change_7_pct"] == 100.0
    assert p["views"]["last_28"] == 2100 and p["views"]["prev_28"] is None
    assert p["views"]["change_28_pct"] is None
    assert p["subs_net"]["last_7"] == 7
    assert p["impressions"]["last_7"] is None and p["ctr"]["last_7"] is None


def test_ctr_is_weighted_not_averaged():
    reach = [ReachRow(d(1), "a", 1000, 100.0), ReachRow(d(2), "b", 10, 5.0)]
    p = compute_progress(days(7), reach, TODAY)
    assert p["impressions"]["last_7"] == 1010
    assert p["ctr"]["last_7"] == pytest.approx(105 / 1010 * 100, rel=1e-3)


def test_avg_view_pct_is_views_weighted():
    rows = [DayRow(d(1), 300, 0, 60.0, 0, 0), DayRow(d(2), 100, 0, 20.0, 0, 0)]
    assert compute_progress(rows, [], TODAY)["avg_view_pct"]["last_7"] == pytest.approx(50.0)


def test_empty_channel_all_inconclusive_no_crash():
    findings = run()
    assert [f.code for f in findings] == CODES
    assert all(f.status == "inconclusive" and f.summary for f in findings)
    p = compute_progress([], [], TODAY)
    assert all(v["last_7"] is None and v["change_28_pct"] is None for v in p.values())


# --- rules ------------------------------------------------------------------------------------

def _reach_for(ctrs, impressions=1000):
    return [ReachRow(d(3), vid, impressions, impressions * ctr) for vid, ctr in ctrs.items()]


def test_low_ctr_flags_videos_below_70pct_of_median():
    ctrs = {"a": 0.05, "b": 0.05, "c": 0.05, "d": 0.05, "low": 0.02}
    f = finding(run(reach=_reach_for(ctrs), videos=[video(v) for v in ctrs]), "low_ctr")
    assert f.status == "issue"
    assert [v["video_id"] for v in f.evidence["videos"]] == ["low"]
    assert f.evidence["median_ctr"] == pytest.approx(5.0)
    assert f.action


def test_low_ctr_inconclusive_with_few_videos():
    f = finding(run(reach=_reach_for({"a": 0.05, "b": 0.01}) + _reach_for({"tiny": 0.1}, impressions=100)), "low_ctr")
    assert f.status == "inconclusive" and "have 2" in f.summary


def test_ctr_trend_drop_is_issue():
    reach = [ReachRow(d(5), "a", 3000, 60.0), ReachRow(d(40), "a", 3000, 120.0)]
    f = finding(run(reach=reach), "ctr_trend")
    assert f.status == "issue" and f.evidence["change_pct"] == pytest.approx(-50.0)


def test_ctr_trend_inconclusive_with_few_impressions():
    reach = [ReachRow(d(5), "a", 1500, 60.0), ReachRow(d(40), "a", 3000, 120.0)]
    assert finding(run(reach=reach), "ctr_trend").status == "inconclusive"


def _curve(r30):
    return [(0.0, 1.0), (0.1, 0.8), (0.3, r30), (0.5, r30 * 0.8), (1.0, 0.1)]


def test_early_dropoff_flags_weak_30pct_retention():
    vids = [video(v) for v in ("a", "b", "c", "weak")]
    retention = {"a": _curve(0.5), "b": _curve(0.5), "c": _curve(0.5), "weak": _curve(0.2)}
    f = finding(run(videos=vids, retention=retention), "early_dropoff")
    assert f.status == "issue"
    assert [v["video_id"] for v in f.evidence["videos"]] == ["weak"]
    assert f.evidence["median_at_30"] == pytest.approx(0.5)


def test_early_dropoff_inconclusive_with_two_curves():
    f = finding(run(videos=[video("a"), video("b")], retention={"a": _curve(0.5), "b": _curve(0.5)}), "early_dropoff")
    assert f.status == "inconclusive"


def test_view_pct_by_format_medians():
    vids = [video(f"l{i}", pct=40.0 + i) for i in range(3)] + [video(f"s{i}", short=True, pct=80.0) for i in range(3)]
    f = finding(run(videos=vids), "view_pct_by_format")
    assert f.status == "info" and f.evidence == {"long": 41.0, "short": 80.0}


def test_view_pct_by_format_inconclusive():
    assert finding(run(videos=[video("a"), video("s", short=True)]), "view_pct_by_format").status == "inconclusive"


def test_traffic_dependence_and_shift():
    traffic = [TrafficRow(d(2), "BROWSE", 700), TrafficRow(d(2), "YT_SEARCH", 300),
               TrafficRow(d(40), "BROWSE", 300), TrafficRow(d(40), "YT_SEARCH", 700)]
    f = finding(run(traffic=traffic), "traffic_mix")
    assert f.status == "issue" and f.evidence["top_source"] == "BROWSE"
    assert f.evidence["shares"]["BROWSE"] == pytest.approx(70.0)
    assert {s["source"] for s in f.evidence["shifts"]} == {"BROWSE", "YT_SEARCH"}


def test_traffic_inconclusive_under_500_views():
    assert finding(run(traffic=[TrafficRow(d(2), "BROWSE", 499)]), "traffic_mix").status == "inconclusive"


def test_sub_conversion_per_1000_views():
    rows = days(28, views=100, gained=3, lost=1) + days(28, views=100, start=29, gained=2, lost=1)
    vids = [video("good", views=1000, subs=20), video("meh", views=1000, subs=1), video("tiny", views=100, subs=50)]
    f = finding(run(days=rows, videos=vids), "sub_conversion")
    assert f.status == "info"
    assert f.evidence["last_28"] == pytest.approx(20.0) and f.evidence["prev_28"] == pytest.approx(10.0)
    assert f.evidence["best_videos"][0]["video_id"] == "good"
    assert "tiny" not in [v["video_id"] for v in f.evidence["best_videos"]]


def test_sub_conversion_inconclusive_under_1000_views():
    assert finding(run(days=days(28, views=10)), "sub_conversion").status == "inconclusive"


def test_cadence_compares_upload_weeks():
    rows = days(26 * 7, views=100)
    # uploads in 5 of the 26 weeks; those weeks get 300 views/day
    upload_weeks = [0, 2, 4, 6, 8]
    for w in upload_weeks:
        for i in range(7):
            rows[w * 7 + i] = DayRow(rows[w * 7 + i].day, 300, 0, 40.0, 0, 0)
    vids = [video(f"u{w}", age=w * 7 + 1) for w in upload_weeks]
    f = finding(run(days=rows, videos=vids), "cadence")
    assert f.status == "info" and f.evidence["ratio"] == pytest.approx(3.0)
    assert f.evidence["weeks_with_upload"] == 5 and f.evidence["weeks_without"] == 21


def test_cadence_inconclusive_without_enough_weeks():
    assert finding(run(days=days(14), videos=[video("a", age=2)]), "cadence").status == "inconclusive"


def test_title_keywords_beat_and_lag():
    vids = [video(f"r{i}", age=10, views=3000, title=f"Sunday reset routine {i}") for i in range(3)]
    vids += [video(f"m{i}", age=10, views=200, title=f"Motivation speech {i}") for i in range(3)]
    vids += [video(f"x{i}", age=10, views=1000, title=f"Plain talk number {i}") for i in range(5)]
    f = finding(run(videos=vids), "title_keywords")
    assert f.status == "info"
    assert "reset" in [k["keyword"] for k in f.evidence["beat"]]
    assert "motivation" in [k["keyword"] for k in f.evidence["lag"]]


def test_title_keywords_inconclusive_under_10_videos():
    assert finding(run(videos=[video(f"v{i}") for i in range(9)]), "title_keywords").status == "inconclusive"


def _on_weekday(vid, weekday, views):
    base = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)  # a Monday
    return video(vid, views=views, published=base + timedelta(days=weekday, weeks=int(vid[1:]) % 4))


def test_publish_timing_good_with_clear_gap():
    vids = [_on_weekday(f"a{i}", 0, 3000) for i in range(10)] + [_on_weekday(f"b{i}", 3, 1000) for i in range(10)]
    f = finding(run(videos=vids), "publish_timing")
    assert f.status == "good" and f.evidence["best_day"] == "Monday"


def test_publish_timing_inconclusive_under_20_videos():
    f = finding(run(videos=[video(f"v{i}") for i in range(19)]), "publish_timing")
    assert f.status == "inconclusive" and f.summary == "No reliable data on the best publish day yet."


def test_publish_timing_inconclusive_without_clear_gap():
    vids = [_on_weekday(f"a{i}", 0, 1200) for i in range(10)] + [_on_weekday(f"b{i}", 3, 1000) for i in range(10)]
    assert finding(run(videos=vids), "publish_timing").status == "inconclusive"
