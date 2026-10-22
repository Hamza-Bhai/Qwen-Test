"""Phase 2 — unit tests for the deterministic audit engine.

Synthetic fixtures live ONLY inside tests (never served as production data).
Every assertion checks a documented formula from app.analytics.audit.FORMULAS.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.audit import (
    AuditConfig,
    VideoRanking,
    classify_format,
    compute_health_score,
    compute_performance,
    detect_outliers,
    group_topics,
    rank_videos,
)

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
CFG = AuditConfig()


class FakeVideo:
    """Minimal stand-in with the same attribute names the engine reads."""

    def __init__(self, vid="v", title="Some title here", views=0, likes=None,
                 comments=None, days_ago=10, duration=None, description="desc"):
        self.youtube_video_id = vid
        self.title = title
        self.view_count = views
        self.like_count = likes
        self.comment_count = comments
        self.published_at = NOW - timedelta(days=days_ago)
        self.duration_seconds = duration
        self.thumbnail_url = None
        self.description = description


def _rank(vid, vpd):
    return VideoRanking(vid, vid, None, NOW, int(vpd * 30), None, None, None,
                        30, vpd, None, "long")


# --------------------------------------------------------------- averages/median
class TestAveragesAndMedians:
    def test_avg_median_total(self):
        vids = [FakeVideo(views=v, days_ago=10) for v in (10, 20, 30, 40, 50)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.videos_analyzed == 5
        assert perf.total_views == 150
        assert perf.avg_views == 30
        assert perf.median_views == 30

    def test_even_count_median_is_mean_of_middle_two(self):
        vids = [FakeVideo(views=v, days_ago=10) for v in (10, 20, 30, 40)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.median_views == 25

    def test_empty_set_yields_none_not_crash(self):
        perf = compute_performance([], CFG, NOW)
        assert perf.avg_views is None and perf.median_views is None
        assert perf.total_views == 0
        assert perf.uploads_per_month is None
        assert perf.days_since_last_upload is None


# ------------------------------------------------------------------ zero/hidden
class TestZeroViewsAndUnavailable:
    def test_zero_views_ratios_are_none_not_divide_by_zero(self):
        vids = [FakeVideo(views=0, likes=5, comments=1, days_ago=5)]
        perf = compute_performance(vids, CFG, NOW)  # must not raise
        assert perf.total_views == 0
        assert perf.like_to_view_ratio is None  # 5 likes / 0 views -> undefined

    def test_hidden_likes_excluded_from_engagement(self):
        vids = [
            FakeVideo(views=1000, likes=50, comments=5, days_ago=10),
            FakeVideo(views=999_999, likes=None, comments=None, days_ago=10),
        ]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.likes_available_on == 1
        assert perf.hidden_stats_videos == 1
        # ratio uses ONLY the video exposing likes: 50/1000
        assert perf.like_to_view_ratio == pytest.approx(0.05)
        assert perf.comment_to_view_ratio == pytest.approx(0.005)

    def test_all_likes_hidden(self):
        vids = [FakeVideo(views=100, likes=None, comments=None, days_ago=3)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.total_likes is None
        assert perf.like_to_view_ratio is None


# ---------------------------------------------------------------- age normalization
class TestAgeNormalization:
    def test_same_views_different_age_gives_different_vpd(self):
        old = FakeVideo(vid="old", views=10_000, days_ago=100)
        new = FakeVideo(vid="new", views=10_000, days_ago=10)
        rows, base = rank_videos([old, new], CFG, NOW)
        by_id = {r.youtube_video_id: r for r in rows}
        assert by_id["new"].views_per_day == 1000.0
        assert by_id["old"].views_per_day == 100.0
        assert by_id["new"].vs_baseline_pct > 0 > by_id["old"].vs_baseline_pct

    def test_age_floor_one_day_for_today_uploads(self):
        fresh = FakeVideo(vid="fresh", views=500, days_ago=0)
        rows, _ = rank_videos([fresh], CFG, NOW)
        assert rows[0].age_days == 1  # never divide by zero age
        assert rows[0].views_per_day == 500.0

    def test_missing_publish_date_does_not_crash(self):
        v = FakeVideo(views=100)
        v.published_at = None
        rows, _ = rank_videos([v], CFG, NOW)
        assert rows[0].age_days >= 1


# -------------------------------------------------------------------- cadence
class TestUploadCadence:
    def test_uploads_per_month_over_span(self):
        # 12 uploads, one per week -> span exactly 77 days -> 12/(77/30.44)
        vids = [FakeVideo(views=100, days_ago=77 - i * 7) for i in range(12)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.uploads_per_month == pytest.approx(12 / (77 / 30.44), rel=0.02)

    def test_single_upload_cadence_unknown(self):
        perf = compute_performance([FakeVideo()], CFG, NOW)
        assert perf.uploads_per_month is None

    def test_days_since_last_upload(self):
        vids = [FakeVideo(days_ago=7), FakeVideo(days_ago=40)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.days_since_last_upload == 7


# --------------------------------------------------------------------- outliers
class TestOutlierDetection:
    def _rows(self, vpds):
        return [_rank(f"v{i}", x) for i, x in enumerate(vpds)]

    def test_extreme_high_performer_flagged(self):
        # Spread among the bulk so IQR > 0 (identical values => zero IQR => no fence).
        rows = self._rows(list(range(5, 20)) + [5000])
        out = detect_outliers(rows)
        assert [r.youtube_video_id for r in out] == ["v15"]

    def test_no_false_positive_on_uniform_data(self):
        rows = self._rows([100] * 12)
        assert detect_outliers(rows) == []

    def test_small_sample_never_flags(self):
        rows = self._rows([10, 10, 10, 9999])
        assert detect_outliers(rows) == []  # <8 videos => fences meaningless


# ----------------------------------------------------------------- formats
class TestFormatClassification:
    def test_short_long_unknown(self):
        assert classify_format(120, CFG.short_max_seconds) == "shorts"
        assert classify_format(300, CFG.short_max_seconds) == "shorts"
        assert classify_format(301, CFG.short_max_seconds) == "long"
        assert classify_format(None, CFG.short_max_seconds) == "unknown"

    def test_shorts_vs_long_aggregates(self):
        vids = [
            FakeVideo(views=900, days_ago=10, duration=60),
            FakeVideo(views=900, days_ago=10, duration=90),
            FakeVideo(views=100, days_ago=10, duration=1200),
            FakeVideo(views=100, days_ago=10, duration=1500),
            FakeVideo(views=50, days_ago=10, duration=None),
        ]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.shorts_count == 2 and perf.longform_count == 2
        assert perf.format_unclassified == 1
        assert perf.shorts_avg_views == 900 and perf.longform_avg_views == 100

    def test_avg_duration_is_video_length_not_watch_time(self):
        # avg_duration_seconds averages content length; watch time is NOT public.
        vids = [FakeVideo(duration=60), FakeVideo(duration=180)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.avg_duration_seconds == 120
        assert not hasattr(perf, "avg_watch_time_seconds")


# ------------------------------------------------------------------- topics
class TestTopicGrouping:
    def test_repeated_token_forms_group_with_baseline(self):
        rows = [
            _rank("a", 50), _rank("b", 60), _rank("c", 55),
            _rank("d", 10), _rank("e", 12),
        ]
        for i, t in enumerate(("drone review one", "drone review two", "drone review three",
                               "cooking basics", "cooking tips")):
            rows[i].title = t
        strong, weak = group_topics(rows, baseline=30.0)
        drone = next(g for g in strong if g.topic == "drone")
        assert drone.videos == 3
        assert drone.vs_channel_baseline_pct == pytest.approx((55 / 30 - 1) * 100, abs=0.2)

    def test_min_three_videos_required(self):
        rows = [_rank("a", 50), _rank("b", 60)]
        rows[0].title = rows[1].title = "quantum thing"
        strong, weak = group_topics(rows, baseline=55.0)
        assert all(g.topic != "quantum" for g in strong + weak)


# ---------------------------------------------------------------- health score
class TestHealthScore:
    def _perf(self, **kw):
        defaults = dict(
            videos_analyzed=10, analyzed_from_total_synced=10, total_views=1000,
            avg_views=100.0, median_views=90.0, stdev_views=None, total_likes=50,
            total_comments=5, like_to_view_ratio=0.05, comment_to_view_ratio=0.005,
            likes_available_on=10, comments_available_on=10, hidden_stats_videos=0,
            uploads_per_month=4.0, days_since_last_upload=5, first_published_at=NOW,
            last_published_at=NOW, avg_duration_seconds=None, median_duration_seconds=None,
            duration_known_on=0, shorts_count=0, longform_count=0, format_unclassified=10,
            shorts_avg_views=None, longform_avg_views=None, shorts_median_vpd=None,
            longform_median_vpd=None, distribution_buckets=[], monthly_uploads=[],
        )
        defaults.update(kw)
        from app.analytics.audit import PerformanceStats
        return PerformanceStats(**defaults)

    def test_score_within_bounds_and_documented_weights(self):
        findings = [
            type("F", (), {"key": "upload_consistency", "status": "ok",
                           "evidence": {"median_gap_days": 7, "gaps_over_3x_median": 0,
                                        "total_gaps": 9}})(),
            type("F", (), {"key": "recent_trend", "status": "ok",
                           "evidence": {"recent_median_vpd": 100, "baseline_median_vpd": 100}})(),
            type("F", (), {"key": "concentration", "status": "ok",
                           "evidence": {"top5_share_of_views": 0.3}})(),
        ]
        hs = compute_health_score(findings, self._perf(), 100.0, [_rank("a", 10)], NOW, CFG)
        assert hs.score is not None and 0 <= hs.score <= 100
        assert hs.band in ("healthy", "watch", "at-risk")
        names = {c["name"] for c in hs.components}
        assert names == {"recency", "consistency", "recent_trend", "distribution", "engagement"}
        assert any("Weights:" in c for c in hs.caveats)

    def test_missing_components_excluded_not_penalized(self):
        # No findings at all + no likes available => only recency scores.
        hs = compute_health_score([], self._perf(like_to_view_ratio=None, total_likes=None),
                                  None, [], NOW, CFG)
        assert [c["name"] for c in hs.components] == ["recency"]
        assert hs.score == 100  # full marks on the sole available component
        assert any("excludes components lacking data" in c for c in hs.caveats)

    def test_no_data_returns_none_score(self):
        perf = self._perf(days_since_last_upload=None, like_to_view_ratio=None)
        hs = compute_health_score([], perf, None, [], NOW, CFG)
        assert hs.score is None
        assert hs.band == "insufficient-data"
