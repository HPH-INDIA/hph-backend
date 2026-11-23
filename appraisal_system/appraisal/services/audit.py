from models import AppraisalEvent, db


def record_event(
    cycle_id, actor_user_id, action, appraisal=None, from_status=None, to_status=None, details=None
):
    """Add an audit row to the current transaction (NFR-3); the caller commits."""
    db.session.add(
        AppraisalEvent(
            cycle_id=cycle_id,
            appraisal_id=appraisal.id if appraisal is not None else None,
            actor_user_id=actor_user_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            details=details,
        )
    )
