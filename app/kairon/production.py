"""Completed-production identity used by the Daily Refresh HTML dashboard."""
from sqlalchemy import case, func

from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch


def unique_completed_production():
    """Count MBI + level + completed date once, regardless of task creation.

    Select globally before caller filters, so duplicate tasks assigned to
    different coders/programs cannot inflate group totals. The oldest stored
    eligible record wins deterministically. Legacy records without an MBI
    fingerprint remain separate: their beneficiary identity is unknown.
    Source records are retained and remain accessible by ID for audit.
    """
    record = KaironChartRecord
    ids = (
        db.session.query(func.min(record.id))
        .join(KaironUploadBatch, record.batch_id == KaironUploadBatch.id)
        .filter(
            record.status == "Completed",
            record.completed_date.isnot(None),
            KaironUploadBatch.superseded_at.is_(None),
        )
        .group_by(
            record.mbi_fingerprint,
            record.level,
            record.completed_date,
            case((record.mbi_fingerprint.is_(None), record.id), else_=None),
        )
        .correlate(None)
    )
    return record.id.in_(ids)
