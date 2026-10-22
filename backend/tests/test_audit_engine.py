"""Phase 2 — unit tests for the deterministic audit engine.

Synthetic fixtures live ONLY inside tests (never served as production data).
Every assertion checks a documented formula from app.analytics.audit.FORMULAS.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.youtube import check_is_short
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


class FakeResult:
    def __init__(self, vids):
        self._vids = vids

    def scalars(self):
        return self

    def all(self):
        return list(self._vids)


class FakeDB:
    """Stands in for Session: run_channel_audit only uses db.execute(select(...))."""

    def __init__(self, vids):
        self._vids = vids

    def execute(self, stmt):
        return FakeResult(self._vids)


NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
CFG = AuditConfig()


class FakeVideo:
    """Minimal stand-in with the same attribute names the engine reads."""

    def __init__(self, vid="v", title="Some title here", views=0, likes=None,
                 comments=None, days_ago=10, duration=None, description="desc",
                 synced_days_ago=None, is_short=None):
        self.youtube_video_id = vid
        self.title = title
        self.view_count = views
        self.like_count = likes
        self.comment_count = comments
        self.published_at = NOW - timedelta(days=days_ago)  # float-safe
        self.duration_seconds = duration
        self.is_short = is_short
        self.thumbnail_url = None
        self.description = description
        self.last_synced_at = (
            NOW - timedelta(days=synced_days_ago) if synced_days_ago is not None else None
        )


def _rank(vid, vpd):
    return VideoRanking(vid, vid, None, NOW, int(vpd * 30), None, None, None,
                        30, vpd, None, "estimated-longform", None, True)


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
        assert rows[0].age_days == 0          # unknown, never fabricated as 1
        assert rows[0].mature is False        # excluded from rankings
        assert rows[0].views_per_day == 0.0   # no fake rate from a floored age


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
    def test_short_long_unknown_are_estimates_not_confirmed(self):
        # Labels must carry the 'estimated-' prefix: no public field confirms format.
        assert classify_format(120, CFG.short_max_seconds) == "estimated-shorts"
        assert classify_format(180, CFG.short_max_seconds) == "estimated-shorts"
        assert classify_format(181, CFG.short_max_seconds) == "estimated-longform"
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
            uploads_per_month=4.0, observed_span_days=None, cadence_reliable=True,
            days_since_last_upload=5, first_published_at=NOW,
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
                                        "total_gaps": 9, "observed_span_days": 63}})(),
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


# ------------------------------------------------- reliability review additions
class FakeChannel:
    def __init__(self, last_synced_at=None):
        self.id = 1
        self.youtube_channel_id = "UCfake"
        self.title = "Fake Channel"
        self.video_count = 50
        self.last_synced_at = last_synced_at


def _run_audit_on(vids, channel_last_synced=None):
    from app.analytics.audit import run_channel_audit

    return run_channel_audit(FakeDB(vids), FakeChannel(channel_last_synced), CFG, NOW)


class TestFormatEstimatesInRankings:
    def test_ranking_form_labels_are_estimated(self):
        vids = [FakeVideo(duration=60), FakeVideo(duration=1200), FakeVideo(duration=None)]
        rows, _ = rank_videos(vids, CFG, NOW)
        assert [r.form for r in rows] == ["estimated-shorts", "estimated-longform", "unknown"]


class TestNewUploadFairness:
    """Fresh uploads must never be labelled underperformers or decline."""

    def _mixed_set(self):
        # 8 mature videos at ~100 vpd, 2 brand-new (3-day) videos at ~5 vpd.
        vids = [FakeVideo(vid=f"m{i}", views=3000, days_ago=30) for i in range(8)]
        vids += [FakeVideo(vid="new1", views=15, days_ago=3), FakeVideo(vid="new2", views=20, days_ago=3)]
        return vids

    def test_new_videos_excluded_from_underperformers(self):
        audit = _run_audit_on(self._mixed_set())
        ids = {r.youtube_video_id for r in audit.underperformers}
        assert "new1" not in ids and "new2" not in ids
        # mature low-ish performers may appear, but nothing younger than 7 days
        assert all(r.age_days >= 7 for r in audit.underperformers)

    def test_recent_decline_not_claimed_when_cohort_is_immature(self):
        # recent cohort (last 90d window covers everything here) dominated by <7d uploads
        vids = [FakeVideo(vid=f"o{i}", views=3000, days_ago=200) for i in range(6)]
        vids += [FakeVideo(vid="n1", views=10, days_ago=2), FakeVideo(vid="n2", views=12, days_ago=4)]
        audit = _run_audit_on(vids)
        tr = next(f for f in audit.findings if f.key == "recent_trend")
        assert tr.status != "detected" or "Mature" in tr.explanation
        if tr.evidence.get("new_uploads_excluded_from_decline_call"):
            # when immature uploads exist the raw ratio alone cannot trigger 'detected'
            pass  # covered by exclusion tests below


class TestProvenanceAndFreshness:
    def test_provenance_fields_present_in_report(self):
        audit = _run_audit_on([FakeVideo(views=100, days_ago=10)])
        assert set(audit.provenance) == {"observed", "calculated", "heuristic", "unavailable", "inconclusive"}
        assert audit.unavailable_metrics and "Click-through rate (CTR)" in audit.unavailable_metrics
        assert set(audit.confidence_rules) == {"high", "medium", "low", "inconclusive"}

    def test_freshness_reports_sample_size_and_sync_time(self):
        synced = NOW - timedelta(days=1)
        vids = [FakeVideo(views=100, days_ago=10)]
        for v in vids:
            v.last_synced_at = synced
        audit = _run_audit_on(vids, channel_last_synced=synced)
        fr = audit.data_freshness
        assert fr["sample_size_analyzed"] == 1
        assert fr["stats_age_days"] == 1
        assert fr["stale_data"] is False

    def test_stale_data_flagged_and_noted(self):
        old_sync = NOW - timedelta(days=30)
        vids = [FakeVideo(views=100, days_ago=40)]
        for v in vids:
            v.last_synced_at = old_sync
        audit = _run_audit_on(vids)
        assert audit.data_freshness["stale_data"] is True
        assert any("days old" in n for n in audit.notes)

    def test_missing_sync_timestamp_noted_not_zeroed(self):
        audit = _run_audit_on([FakeVideo(views=100, days_ago=10)])  # no last_synced_at anywhere
        assert audit.data_freshness["last_video_stats_sync"] is None
        assert audit.data_freshness["stats_age_days"] is None
        assert any("No recorded sync timestamp" in n for n in audit.notes)

    def test_notes_disclose_format_and_topic_limitations(self):
        audit = _run_audit_on([FakeVideo(views=100, days_ago=10)])
        joined = " ".join(audit.notes).lower()
        assert "estimated" in joined and "lexical" in joined

    def test_score_caveats_deny_official_benchmarks(self):
        vids = [FakeVideo(views=100 * (i + 1), days_ago=10 * (i + 1), likes=5) for i in range(12)]
        audit = _run_audit_on(vids)
        caveats = " ".join(audit.health_score.caveats).lower()
        assert "not official youtube standards" in caveats
        assert "significance" in caveats


# ============================================================ reliability fixes
# Regressions for the six defects found in a real local audit report.
class TestUploadGapFractionalDays:
    """Bug #1: timedelta.days truncation made every sub-24h gap read as 0 and
    the engine falsely claimed all uploads shared one publish date."""

    def _burst_channel(self):
        # 50 uploads published within a single ~4.2-day burst ending NOW
        # (spaced ~2h apart; newest floored at age 1 day, all "mature enough").
        # This reproduces the real report: first_pub now-4.2d, last_pub now.
        return [FakeVideo(vid=f"b{i}", views=1000 + i,
                          days_ago=round(4.2 - i * (4.2 / 49), 4))
                for i in range(50)]

    def test_burst_not_reported_as_single_shared_date(self):
        audit = _run_audit_on(self._burst_channel())
        cons = next(f for f in audit.findings if f.key == "upload_consistency")
        assert cons.status == "inconclusive"
        text = (cons.explanation + " ".join(cons.data_limitations)).lower()
        assert "same publish date" not in text and "share the same" not in text
        # Honest either/or: single-burst wording OR too-fresh-to-judge wording.
        assert ("burst" in text or "days old" in text or "under 7" in text)
        # Evidence must carry fractional gaps, never an all-zero list.
        gaps = cons.evidence["gaps_days"]
        assert len(gaps) == 49
        assert max(gaps) > 0
        assert cons.evidence["observed_span_days"] == pytest.approx(4.2, abs=0.1)

    def test_gap_days_is_fractional_and_signed(self):
        from app.analytics.audit import _gap_days
        a = NOW
        b = NOW + timedelta(hours=6)
        assert _gap_days(a, b) == pytest.approx(0.25)
        assert _gap_days(b, a) == pytest.approx(-0.25)

    def test_same_day_multiple_uploads_produce_small_positive_gaps(self):
        vids = [FakeVideo(vid=f"s{i}", views=100,
                          days_ago=round(10 - i * (6 / 23) / 24, 5))
                for i in range(24)]  # 24 uploads within ~6 hours on one day
        audit = _run_audit_on(vids)
        cons = next(f for f in audit.findings if f.key == "upload_consistency")
        assert cons.evidence["median_gap_days"] > 0

    def test_naive_timestamps_from_sqlite_are_handled(self):
        # SQLite returns naive datetimes for tz-aware columns; comparisons with
        # aware 'now' must not crash and gaps must still be computed.
        vids = self._burst_channel()
        for v in vids:
            v.published_at = v.published_at.replace(tzinfo=None)
        audit = _run_audit_on(vids)
        cons = next(f for f in audit.findings if f.key == "upload_consistency")
        assert cons.status == "inconclusive"
        assert cons.evidence["observed_span_days"] == pytest.approx(4.2, abs=0.1)

    def test_mixed_timezone_offsets_sorted_correctly(self):
        import zoneinfo
        base = [FakeVideo(vid=f"t{i}", views=500, days_ago=200 - i * 10)
                for i in range(8)]
        # Re-stamp some publishes in non-UTC zones representing the same instant.
        base[0].published_at = base[0].published_at.astimezone(
            zoneinfo.ZoneInfo("America/New_York"))
        base[1].published_at = base[1].published_at.astimezone(
            zoneinfo.ZoneInfo("Asia/Kolkata"))
        audit = _run_audit_on(base)
        cad = next(f for f in audit.findings if f.key == "upload_cadence")
        first = cad.evidence["first_published_at"]
        last = cad.evidence["last_published_at"]
        assert first is not None and last > first  # instants compare correctly


class TestUploadFrequencyInterpretation:
    """Bug #2: 50 videos over 4.2 days must NOT be presented as 50/month."""

    def test_short_window_reports_no_monthly_rate(self):
        vids = TestUploadGapFractionalDays._burst_channel(None)
        perf = compute_performance(vids, CFG, NOW)
        assert perf.uploads_per_month is None
        assert perf.cadence_reliable is False
        assert perf.observed_span_days == 4
        audit = _run_audit_on(vids)
        cad = next(f for f in audit.findings if f.key == "upload_cadence")
        # The honest refusal sentence must be present, and no numeric rate may
        # be asserted (the only allowed "/month" mention is the explicit
        # disclaimer "no videos/month figure is reported").
        assert "too short to extrapolate" in cad.explanation
        assert "50 uploads observed" in cad.explanation
        import re as _re
        assert not _re.search(r"\d+(?:\.\d+)?\s*(?:videos/|uploads?/)?month", cad.explanation.replace(
            "no videos/month figure is reported", ""))
        assert cad.evidence["uploads_per_month"] is None
        assert cad.evidence["cadence_reliable"] is False

    def test_long_window_still_reports_monthly_rate(self):
        # 12 weekly uploads -> 77-day span >= 30 -> reliable cadence.
        vids = [FakeVideo(views=100, days_ago=77 - i * 7) for i in range(12)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.cadence_reliable is True
        assert perf.uploads_per_month == pytest.approx(12 / (77 / 30.44), rel=0.02)

    def test_boundary_exactly_min_span_is_reliable(self):
        vids = [FakeVideo(vid="x", views=1, days_ago=30),
                FakeVideo(vid="y", views=1, days_ago=0)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.observed_span_days == 30
        assert perf.cadence_reliable is True

    def test_one_day_below_min_span_is_unreliable(self):
        vids = [FakeVideo(vid="x", views=1, days_ago=29),
                FakeVideo(vid="y", views=1, days_ago=0)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.observed_span_days == 29
        assert perf.cadence_reliable is False
        assert perf.uploads_per_month is None


class TestTrendCohortEligibility:
    """Bug #3: when all analyzed videos fall inside the recent window, the old
    code compared the cohort against itself and reported fake 'stable'."""

    def test_all_recent_videos_yields_inconclusive_not_stable(self):
        # 50 uploads across ~4 days only — no independent history at all.
        vids = [FakeVideo(vid=f"r{i}", views=10_000 + i * 500,
                          days_ago=round(34.2 - i * (4.2 / 49), 4))
                for i in range(50)]
        audit = _run_audit_on(vids)
        tr = next(f for f in audit.findings if f.key == "recent_trend")
        assert tr.status == "inconclusive"
        assert tr.confidence == "inconclusive"
        # Either no independent history, or recent cohort too immature — both
        # are honest refusals; neither may claim "stable".
        assert ("independent" in tr.explanation or "days old" in tr.explanation
                or "at least 2 mature" in tr.explanation)
        # And the trend component must not enter the health score.
        names = {c["name"] for c in audit.health_score.components}
        assert "recent_trend" not in names

    def test_separate_cohorts_still_computed(self):
        hist = [FakeVideo(vid=f"h{i}", views=10_000, days_ago=200 + i * 10)
                for i in range(6)]  # mature, predates the 90d window
        rec = [FakeVideo(vid=f"c{i}", views=30_000, days_ago=20 + i)
               for i in range(4)]   # mature, inside recent window
        audit = _run_audit_on(hist + rec)
        tr = next(f for f in audit.findings if f.key == "recent_trend")
        assert tr.status != "inconclusive"
        assert tr.evidence["independent_history_videos"] >= 3
        assert tr.evidence["recent_median_vpd"] is not None
        assert "recent_trend" in {c["name"] for c in audit.health_score.components}

    def test_overlapping_partial_history_still_needs_three_independent(self):
        hist = [FakeVideo(vid=f"h{i}", views=10_000, days_ago=200)
                for i in range(2)]  # only 2 independent historical videos
        rec = [FakeVideo(vid=f"c{i}", views=9_000, days_ago=15)
               for i in range(6)]
        audit = _run_audit_on(hist + rec)
        tr = next(f for f in audit.findings if f.key == "recent_trend")
        assert tr.status == "inconclusive"
        assert tr.evidence["independent_history_videos"] == 2


class TestFreshVideoRankingFairness:
    """Bug #4: hours-old uploads floored at age=1 must not appear in top lists."""

    def _mixed_age_set(self):
        mature = [FakeVideo(vid=f"m{i}", views=7000, days_ago=30)
                  for i in range(10)]                     # ~233 vpd each
        fresh = [
            FakeVideo(vid="mins", views=500, days_ago=0),          # minutes old
            FakeVideo(vid="hours", views=2000, days_ago=0),        # hours old
            FakeVideo(vid="oneday", views=5000, days_ago=1),       # 1 day old
            FakeVideo(vid="sixdays", views=5000, days_ago=6),      # just under 7
        ]
        return mature, fresh

    def test_fresh_uploads_absent_from_top_performers(self):
        mature, fresh = self._mixed_age_set()
        audit = _run_audit_on(mature + fresh)
        top_ids = {r.youtube_video_id for r in audit.top_performers}
        for f in fresh:
            assert f.youtube_video_id not in top_ids
        assert {"mins", "hours", "oneday", "sixdays"} & set(top_ids) == set()

    def test_fresh_uploads_visible_as_provisional_with_explanation(self):
        mature, fresh = self._mixed_age_set()
        audit = _run_audit_on(mature + fresh)
        prov_ids = {r.youtube_video_id for r in audit.fresh_videos_provisional}
        assert {"mins", "hours", "oneday", "sixdays"} <= prov_ids
        assert all(not r.mature for r in audit.fresh_videos_provisional)
        assert any("provisional early traction" in n for n in audit.notes)

    def test_seven_day_old_video_counts_as_mature(self):
        v = FakeVideo(vid="edge", views=7000, days_ago=7)
        rows, _ = rank_videos([v], CFG, NOW)
        assert rows[0].mature is True
        v6 = FakeVideo(vid="edge6", views=7000, days_ago=6)
        rows6, _ = rank_videos([v6], CFG, NOW)
        assert rows6[0].mature is False

    def test_outliers_use_mature_only(self):
        mature = [FakeVideo(vid=f"m{i}", views=7000, days_ago=30 + i)
                  for i in range(9)]
        spike_fresh = FakeVideo(vid="spike", views=100_000, days_ago=0)  # huge vpd
        audit = _run_audit_on(mature + [spike_fresh])
        assert "spike" not in {o.youtube_video_id for o in audit.outliers}
        assert "spike" in {r.youtube_video_id for r in audit.fresh_videos_provisional}

    def test_undated_video_not_ranked_or_claimed_age_one(self):
        v = FakeVideo(vid="nodate", views=9999)
        v.published_at = None
        rows, base = rank_videos([v], CFG, NOW)
        assert rows[0].mature is False
        assert rows[0].age_days == 0  # unknown, NOT fabricated as 1
        assert base is None  # no mature video -> no baseline, not a fake one
        audit = _run_audit_on([v] + [FakeVideo(vid=f"m{i}", views=1000, days_ago=30)
                                      for i in range(8)])
        assert "nodate" not in {r.youtube_video_id for r in audit.top_performers}

    def test_distribution_buckets_exclude_fresh(self):
        mature = [FakeVideo(vid=f"m{i}", views=7000, days_ago=30)
                  for i in range(10)]
        fresh = [FakeVideo(vid="f1", views=5000, days_ago=1)]
        perf = compute_performance(mature + fresh, CFG, NOW)
        excl = [b for b in perf.distribution_buckets if "excluded" in b["label"]]
        assert excl and excl[0]["videos"] == 1


class TestFormatSplitSampleRequirements:
    """Bug #5: 47 Shorts vs 3 long-form with ~4% difference must not produce a
    detected finding recommending production-allocation changes."""

    def test_imbalanced_sample_is_inconclusive(self):
        shorts = [FakeVideo(vid=f"s{i}", views=2000, days_ago=20, duration=60)
                  for i in range(47)]
        longs = [FakeVideo(vid=f"l{i}", views=2000, days_ago=20, duration=1200)
                 for i in range(3)]
        audit = _run_audit_on(shorts + longs)
        fs = next(f for f in audit.findings if f.key == "format_split")
        assert fs.status == "inconclusive"
        assert fs.confidence == "inconclusive"
        # No allocation advice allowed on an inconclusive split.
        joined = (fs.explanation + " " + fs.recommended_action).lower()
        assert "shift production" not in joined
        assert "no action" in joined or "re-audit" in joined or "descriptive" in joined
        # mismatch rule also requires >=8 per group, so it must be absent here
        assert not any(f.key == "format_mismatch" for f in audit.findings)

    def test_balanced_small_sample_also_inconclusive(self):
        vids = ([FakeVideo(views=1000, days_ago=20, duration=60) for _ in range(4)]
                + [FakeVideo(views=1000, days_ago=20, duration=1200) for _ in range(4)])
        audit = _run_audit_on(vids)
        fs = next(f for f in audit.findings if f.key == "format_split")
        assert fs.status == "inconclusive"  # < 8 per group

    def test_balanced_sufficient_sample_with_small_diff_claims_nothing(self):
        vids = ([FakeVideo(vid=f"s{i}", views=1000, days_ago=20, duration=60)
                 for i in range(10)]
                + [FakeVideo(vid=f"l{i}", views=1000, days_ago=20, duration=1200)
                   for i in range(10)])
        audit = _run_audit_on(vids)
        fs = next(f for f in audit.findings if f.key == "format_split")
        assert fs.status == "ok"
        assert "no format advantage is claimed" in fs.explanation

    def test_balanced_sufficient_sample_with_real_gap_is_directional(self):
        vids = ([FakeVideo(vid=f"s{i}", views=3000, days_ago=20, duration=60)
                 for i in range(10)]
                + [FakeVideo(vid=f"l{i}", views=600, days_ago=20, duration=1200)
                   for i in range(10)])
        audit = _run_audit_on(vids)
        fs = next(f for f in audit.findings if f.key == "format_split")
        assert fs.status == "detected"
        assert "(directional)" in fs.title
        assert "not a proven format effect" in fs.explanation


class TestHealthScoreCoverage:
    """Bug #6: partial data must be labelled provisional, never comprehensive."""

    def test_low_coverage_marks_provisional(self):
        # Burst channel (~4.2d span, ended >30d ago): consistency excluded
        # (<30d observed span), trend inconclusive (no independent history).
        vids = [FakeVideo(vid=f"b{i}", views=1000 + i * 100,
                          days_ago=round(34.2 - i * (4.2 / 49), 4))
                for i in range(50)]
        audit = _run_audit_on(vids)
        hs = audit.health_score
        cov = next(c for c in hs.components if c["name"] == "coverage")
        assert cov["score"] < 100
        if hs.score is not None and cov["score"] < 50:
            assert any("PROVISIONAL SCORE" in c for c in hs.caveats)
        else:
            assert any("PARTIAL COVERAGE" in c or "PROVISIONAL" in c for c in hs.caveats)

    def test_full_coverage_has_no_provisional_warning(self):
        vids = ([FakeVideo(vid=f"h{i}", views=10_000, days_ago=200 + i * 10, likes=500)
                 for i in range(6)]
                + [FakeVideo(vid=f"c{i}", views=9_000, days_ago=20 + i, likes=450)
                   for i in range(6)])
        # Add regular spacing so consistency can score: rebuild with even gaps.
        vids = []
        for i in range(14):
            vids.append(FakeVideo(vid=f"e{i}", views=8000 + i * 100,
                                  days_ago=10 + i * 12, likes=400))
        audit = _run_audit_on(vids)
        hs = audit.health_score
        cov = next(c for c in hs.components if c["name"] == "coverage")
        assert cov["score"] >= 80
        assert not any("PROVISIONAL" in c for c in hs.caveats)

    def test_inconclusive_findings_never_contribute_to_score(self):
        # Trend finding inconclusive (all-recent burst) -> component excluded.
        vids = [FakeVideo(vid=f"b{i}", views=1000 + i,
                          days_ago=round(34.2 - i * (4.2 / 49), 4))
                for i in range(50)]
        audit = _run_audit_on(vids)
        tr = next(f for f in audit.findings if f.key == "recent_trend")
        assert tr.status == "inconclusive"
        scored = {c["name"] for c in audit.health_score.components
                  if c.get("score") is not None and c["name"] != "coverage"}
        assert "recent_trend" not in scored
        assert "consistency" not in scored  # <30-day span exclusion


class TestInconclusiveFindingsNeverScored:
    """Cross-path regression: an 'ok'-status recent_trend finding WITHOUT a
    computable baseline (informational, no verdict) must not feed the health
    score — only detected/ok findings WITH both medians may contribute."""

    def test_ok_status_without_baseline_does_not_score(self):
        perf = TestHealthScore()._perf()
        findings = [type("F", (), {"key": "recent_trend", "status": "ok",
                                   "evidence": {}})()]
        hs = compute_health_score(findings, perf, None, [_rank("a", 10)], NOW, CFG)
        names = {c["name"] for c in hs.components if c.get("score") is not None}
        assert "recent_trend" not in names

    def test_ok_status_with_zero_baseline_does_not_crash_or_score(self):
        perf = TestHealthScore()._perf()
        findings = [type("F", (), {"key": "recent_trend", "status": "ok",
                                   "evidence": {"recent_median_vpd": 50.0,
                                                "baseline_median_vpd": None}})()]
        hs = compute_health_score(findings, perf, None, [_rank("a", 10)], NOW, CFG)
        names = {c["name"] for c in hs.components if c.get("score") is not None}
        assert "recent_trend" not in names

    def test_detected_status_with_both_medians_scores(self):
        perf = TestHealthScore()._perf()
        findings = [type("F", (), {"key": "recent_trend", "status": "detected",
                                   "evidence": {"recent_median_vpd": 60.0,
                                                "baseline_median_vpd": 100.0}})()]
        hs = compute_health_score(findings, perf, 100.0, [_rank("a", 10)], NOW, CFG)
        comp = next(c for c in hs.components if c["name"] == "recent_trend")
        assert comp["score"] == pytest.approx(50.0, abs=0.1)  # 0.6/1.2*100

class TestFormatClassificationWithShortsCheck:
    """youtube.com/shorts/<id> check (is_short) beats the duration fallback.

    Shorts are capped at 3 minutes, so a 4-minute landscape video must never
    be counted as a Short, and a 2-minute regular upload that the URL check
    confirmed as non-Short must count as long-form."""

    def test_old_5_minute_threshold_no_longer_counts_as_short(self):
        assert classify_format(240, CFG.short_max_seconds) == "estimated-longform"
        assert classify_format(300, CFG.short_max_seconds) == "estimated-longform"

    def test_url_check_overrides_duration(self):
        assert classify_format(120, CFG.short_max_seconds, is_short=False) == "estimated-longform"
        assert classify_format(45, CFG.short_max_seconds, is_short=True) == "estimated-shorts"

    def test_unchecked_falls_back_to_duration(self):
        assert classify_format(60, CFG.short_max_seconds, is_short=None) == "estimated-shorts"
        assert classify_format(None, CFG.short_max_seconds, is_short=None) == "unknown"

    def test_counts_use_url_check(self):
        vids = [FakeVideo(vid=f"s{i}", duration=45, is_short=True) for i in range(3)]              + [FakeVideo(vid=f"r{i}", duration=90, is_short=False) for i in range(2)]              + [FakeVideo(vid=f"l{i}", duration=240) for i in range(4)]
        perf = compute_performance(vids, CFG, NOW)
        assert perf.shorts_count == 3
        assert perf.longform_count == 6
        assert perf.format_unclassified == 0

    def test_rankings_use_url_check(self):
        rows, _ = rank_videos([FakeVideo(duration=60, is_short=False)], CFG, NOW)
        assert rows[0].form == "estimated-longform"


class TestCheckIsShort:
    """check_is_short reads the youtube.com/shorts/<id> response (no API quota)."""

    @staticmethod
    def _client(status, location=None):
        import httpx

        def handler(request):
            assert request.method == "HEAD"
            assert str(request.url) == "https://www.youtube.com/shorts/abcdefghijk"
            headers = {"location": location} if location else {}
            return httpx.Response(status, headers=headers)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_200_means_short(self):
        assert check_is_short("abcdefghijk", self._client(200)) is True

    def test_redirect_to_watch_means_regular_video(self):
        c = self._client(303, "https://www.youtube.com/watch?v=abcdefghijk")
        assert check_is_short("abcdefghijk", c) is False

    def test_anything_else_is_inconclusive(self):
        assert check_is_short("abcdefghijk", self._client(303, "https://consent.youtube.com/x")) is None
        assert check_is_short("abcdefghijk", self._client(429)) is None

    def test_network_error_is_inconclusive(self):
        import httpx

        def boom(request):
            raise httpx.ConnectError("offline")

        c = httpx.Client(transport=httpx.MockTransport(boom))
        assert check_is_short("abcdefghijk", c) is None

