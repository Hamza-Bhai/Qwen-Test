"""Thin service layer for the Phase-2 audit endpoint.

Routes stay minimal; all math lives in ``app.analytics.audit``. Audits read
only stored records (no YouTube calls, zero quota) and persist a snapshot into
the existing ``channel_analyses.summary`` JSON column — no schema change needed.
"""

from sqlalchemy.orm import Session

from app.analytics.audit import AuditConfig, ChannelAudit, run_channel_audit
from app.db.models import Channel, ChannelAnalysis


def audit_channel(db: Session, channel: Channel, *, limit: int | None = None,
                  persist: bool = True) -> ChannelAudit:
    cfg = AuditConfig() if limit is None else AuditConfig(analyze_limit=limit)
    audit = run_channel_audit(db, channel, cfg)
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
                "health_score": audit.health_score.score,
                "band": audit.health_score.band,
                "findings_detected": [f.key for f in audit.findings if f.status == "detected"],
                "videos_analyzed": audit.performance.videos_analyzed,
            },
        )
        db.add(run)
        db.commit()
    return audit
