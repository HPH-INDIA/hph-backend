from datetime import datetime, timezone

from flask_smorest import abort
from sqlalchemy import or_

from app.auth import has_feature, has_role
from app.cohorts.progression import business_today
from app.extensions import db
from app.lead_coverage.models import LeadCoverage
from app.roles.models import Role, RoleType
from app.users.models import User


def _active_role(user, code):
    today = business_today()
    return bool(
        user and user.is_active and user.role.is_active and has_role(user, code)
        and (user.join_date is None or user.join_date <= today)
        and (user.last_working_day is None or user.last_working_day >= today)
    )


def _eligible_pair(source, covering):
    """Re-evaluate live reporting lines; never rely on the original approval."""
    return bool(
        _active_role(source, "lead") and _active_role(covering, "lead")
        and source.id != covering.id
        and source.project_id is not None and source.project_id == covering.project_id
        and source.reports_to_id is not None and source.reports_to_id == covering.reports_to_id
        and _active_role(source.reports_to, "manager")
    )


def is_active_coverage(record, today=None):
    today = today or business_today()
    return bool(
        record.status == "approved" and record.starts_on <= today <= record.ends_on
        and _eligible_pair(record.source_lead, record.covering_lead)
    )


def active_covered_lead_ids(actor):
    """Only direct grants active *now*, independently of a report's date range."""
    if not _active_role(actor, "lead"):
        return []
    today = business_today()
    rows = LeadCoverage.query.filter(
        LeadCoverage.covering_lead_id == actor.id,
        LeadCoverage.status == "approved",
        LeadCoverage.starts_on <= today,
        LeadCoverage.ends_on >= today,
    ).all()
    return sorted({row.source_lead_id for row in rows if is_active_coverage(row, today)})


def _owned_by_manager(record, actor):
    return bool(
        _active_role(actor, "manager")
        and record.source_lead.reports_to_id == actor.id
        and record.covering_lead.reports_to_id == actor.id
    )


def can_decide(record, actor):
    return bool(
        _active_role(actor, "lead") and actor.id == record.covering_lead_id
        and record.status == "pending" and record.ends_on >= business_today()
        and _eligible_pair(record.source_lead, record.covering_lead)
        and has_feature(actor, "reports", "write")
    )


def can_cancel(record, actor):
    return bool(
        actor and record.status in {"pending", "approved"}
        and has_feature(actor, "reports", "read")
        and (
            (_active_role(actor, "lead") and actor.id in {record.source_lead_id, record.covering_lead_id})
            or _owned_by_manager(record, actor)
        )
    )


def scope_coverages(actor):
    if has_role(actor, "lead"):
        return LeadCoverage.query.filter(or_(
            LeadCoverage.source_lead_id == actor.id,
            LeadCoverage.covering_lead_id == actor.id,
        ))
    owned_ids = db.select(User.id).where(User.reports_to_id == actor.id)
    return LeadCoverage.query.filter(
        LeadCoverage.source_lead_id.in_(owned_ids),
        LeadCoverage.covering_lead_id.in_(owned_ids),
    )


def eligible_leads(actor):
    query = User.query.join(Role).join(RoleType).filter(
        User.is_active.is_(True), Role.is_active.is_(True), RoleType.code == "lead",
        User.project_id.isnot(None),
    )
    if has_role(actor, "lead"):
        if not _active_role(actor.reports_to, "manager"):
            return []
        query = query.filter(
            User.reports_to_id == actor.reports_to_id, User.project_id == actor.project_id,
            User.id != actor.id,
        )
    else:
        query = query.filter(User.reports_to_id == actor.id)
    return [lead for lead in query.order_by(User.first_name, User.last_name, User.id).all()
            if _active_role(lead, "lead")
            and (has_role(actor, "manager") or has_feature(lead, "reports", "write"))]


def _lock_leads(source_id, covering_id):
    # Both participants, ordered identically, serialize reverse/chained requests
    # too (A -> B racing B -> C). Locking just A would miss that race.
    users = User.query.filter(User.id.in_({source_id, covering_id})).order_by(User.id).with_for_update().populate_existing().all()
    by_id = {user.id: user for user in users}
    return by_id.get(source_id), by_id.get(covering_id)


def _check_pair(source, covering):
    if not _eligible_pair(source, covering):
        abort(400, message="Choose two different active leads in the same project reporting to the same active manager.")


def _check_conflicts(source_id, covering_id, starts_on, ends_on, exclude_id=None):
    overlap = LeadCoverage.query.filter(
        LeadCoverage.status == "approved",
        LeadCoverage.starts_on <= ends_on,
        LeadCoverage.ends_on >= starts_on,
        or_(
            LeadCoverage.source_lead_id == source_id,
            LeadCoverage.source_lead_id == covering_id,
            LeadCoverage.covering_lead_id == source_id,
        ),
    )
    if exclude_id is not None:
        overlap = overlap.filter(LeadCoverage.id != exclude_id)
    if overlap.first() is not None:
        abort(409, message="These dates overlap an approved assignment for the absent lead or would create a coverage chain.")


def create_coverage(actor, data):
    source_id = data.get("source_lead_id")
    if has_role(actor, "lead"):
        if source_id is not None and source_id != actor.id:
            abort(403, message="A lead may only request coverage for their own team.")
        source_id = actor.id
    elif source_id is None:
        abort(400, message="Choose the lead whose team needs coverage.")
    elif not has_feature(actor, "reports", "write"):
        abort(403, message="Direct assignment requires write access to the 'reports' feature.")

    starts_on, ends_on = data["starts_on"], data["ends_on"]
    if starts_on < business_today() or ends_on < starts_on:
        abort(400, message="Coverage must start today or later, and end on or after its start date.")
    source, covering = _lock_leads(source_id, data["covering_lead_id"])
    _check_pair(source, covering)
    if not has_feature(covering, "reports", "write"):
        abort(400, message="The covering lead needs write access to Reports to approve requests and review manual records.")
    if any(lead.last_working_day is not None and ends_on > lead.last_working_day for lead in (source, covering)):
        abort(400, message="Coverage must end on or before both leads' last working days.")
    if has_role(actor, "manager") and source.reports_to_id != actor.id:
        abort(403, message="Managers may only assign coverage between their own leads.")
    _check_conflicts(source.id, covering.id, starts_on, ends_on)
    duplicate = LeadCoverage.query.filter(
        LeadCoverage.source_lead_id == source.id,
        LeadCoverage.covering_lead_id == covering.id,
        LeadCoverage.status == "pending",
        LeadCoverage.starts_on <= ends_on,
        LeadCoverage.ends_on >= starts_on,
    ).first()
    if duplicate:
        abort(409, message="A pending request for this lead already overlaps these dates.")

    assigned = has_role(actor, "manager")
    record = LeadCoverage(
        source_lead_id=source.id, covering_lead_id=covering.id,
        starts_on=starts_on, ends_on=ends_on, reason=(data.get("reason") or "").strip() or None,
        status="approved" if assigned else "pending", created_by_id=actor.id,
        decided_by_id=actor.id if assigned else None,
        decided_at=datetime.now(timezone.utc) if assigned else None,
    )
    db.session.add(record)
    db.session.commit()
    return record


def decide_coverage(record_id, actor, action):
    record = scope_coverages(actor).filter(LeadCoverage.id == record_id).first_or_404()
    # Take participant locks before the grant lock everywhere to prevent deadlocks.
    source, covering = _lock_leads(record.source_lead_id, record.covering_lead_id)
    record = LeadCoverage.query.filter_by(id=record_id).with_for_update().populate_existing().one()
    if action == "cancel":
        if record.status not in {"pending", "approved"}:
            abort(409, message="This coverage is already closed.")
        if not can_cancel(record, actor):
            abort(403, message="Only either lead or their manager may cancel this coverage.")
        record.status = "cancelled"
        record.cancelled_by_id = actor.id
        record.cancelled_at = datetime.now(timezone.utc)
    else:
        if actor.id != record.covering_lead_id or not has_role(actor, "lead"):
            abort(403, message="Only the requested covering lead may approve or reject this request.")
        if record.status != "pending":
            abort(409, message="This coverage request is no longer pending.")
        if record.ends_on < business_today():
            abort(409, message="This coverage request has already expired.")
        _check_pair(source, covering)
        if action == "approve":
            _check_conflicts(source.id, covering.id, record.starts_on, record.ends_on, record.id)
        record.status = "approved" if action == "approve" else "rejected"
        record.decided_by_id = actor.id
        record.decided_at = datetime.now(timezone.utc)
    db.session.commit()
    return record
