"""Thin service layer for the Phase-2 audit endpoint.

Routes stay minimal; all math lives in ``app.analytics.audit``. Audits read
only stored records (no YouTube calls, zero quota). Only an explicit
"Re-run audit" (persist=True) stores a snapshot in ``channel_analyses``.
"""

from sqlalchemy.orm import Session

from app.analytics.audit import AuditConfig, ChannelAudit, run_channel_audit
from app.db.models import Channel, ChannelAnalysis


def audit_channel(db: Session, channel: Channel, *, persist: bool = False) -> ChannelAudit:
    audit = run_channel_audit(db, channel, AuditConfig())
    if persist:
        run = ChannelAnalysis(
            channel_id=channel.id,
            trigger="audit",
            subscriber_count=channel.subscriber_count,
            video_count=channel.video_count,
            view_count=channel.view_count,
            videos_synced=audit.performance.videos_analyzed,
            quota_units_used=0,  # audits spend zero YouTube quota
            summary={
                "activity_score": audit.activity_score.score,
                "activity_band": audit.activity_score.band,
                "performance_score": audit.performance_score.score,
                "performance_band": audit.performance_score.band,
                "findings_detected": [f.key for f in audit.findings if f.status == "detected"],
                "videos_analyzed": audit.performance.videos_analyzed,
            },
        )
        db.add(run)
        db.commit()
    return audit
