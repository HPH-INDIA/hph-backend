"""Refresh completed-chart anchors atomically with successful Kairon imports."""
from datetime import datetime, timezone

from flask_smorest import abort
from sqlalchemy import case, text
from sqlalchemy.orm import joinedload

from app.cohorts.models import (
    Cohort, CohortMembership, FoundationTargetRule, StageTargetRule,
    UserStageEvidence, UserStagePeriod,
)
from app.cohorts.progression import business_today, foundation_progress, main_periods, period_on
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.roles.models import Role, RoleType
from app.users.hierarchy import manager_team_user_ids
from app.users.models import Project, User


def coding_users_query():
    return User.query.join(Role).join(RoleType).join(Project, User.project_id == Project.id).filter(
        Project.name == "CODING", RoleType.code.in_(("lead", "employee")))


def refresh_all_user_stages():
    """Rebuild the whole team, including former owners and users without charts.

    The caller commits both the completed batch and its stage refresh. No
    changes to stage evidence are published by an unfinished chunk upload.
    """
    db.session.execute(text("SELECT pg_advisory_xact_lock(740821, 1)"))
    db.session.flush()
    records = db.session.query(
        KaironChartRecord.user_id,
        db.func.min(KaironChartRecord.completed_date),
        db.func.min(case((db.func.upper(KaironChartRecord.program) == "PVP", KaironChartRecord.completed_date))),
        db.func.min(case((db.func.upper(KaironChartRecord.program) == "FOUNDATION", KaironChartRecord.completed_date))),
    ).join(KaironUploadBatch).filter(
        KaironUploadBatch.status == "completed",
        KaironUploadBatch.superseded_at.is_(None),
        KaironChartRecord.status == "Completed",
        KaironChartRecord.completed_date.isnot(None),
        KaironChartRecord.completed_date <= business_today(),
    ).group_by(KaironChartRecord.user_id).all()
    anchors = {r[0]: r[1:] for r in records}
    evidence_by_user = {r.user_id: r for r in UserStageEvidence.query.all()}
    memberships = {m.user_id: m for m in CohortMembership.query.all()}
    now = datetime.now(timezone.utc)
    users = coding_users_query().order_by(User.id).all()
    for user in users:
        evidence = evidence_by_user.get(user.id)
        if evidence is None:
            evidence = UserStageEvidence(user_id=user.id)
            db.session.add(evidence)
        evidence.first_completed, evidence.first_pvp_completed, evidence.first_foundation_completed = anchors.get(user.id, (None, None, None))
        evidence.refreshed_at = now
        rebuild_main_periods(user, evidence=evidence, membership=memberships.get(user.id))
    db.session.flush()
    return len(users)


def rebuild_main_periods(user, evidence=None, membership=None):
    if user.project is None or user.project.name != "CODING" or user.role.role_type.code not in ("lead", "employee"):
        return []
    if membership is None:
        membership = CohortMembership.query.filter_by(user_id=user.id).first()
    if evidence is None:
        evidence = db.session.get(UserStageEvidence, user.id)
    # Before the first refresh/new-user creation there is no observed chart.
    first = evidence.first_completed if evidence else None
    joined = user.join_date or (membership.joined_on if membership else None)
    periods = main_periods(joined, first, user.last_working_day)
    UserStagePeriod.query.filter_by(user_id=user.id).delete(synchronize_session="fetch")
    rows = [UserStagePeriod(user_id=user.id, **p) for p in periods]
    db.session.add_all(rows)
    return rows


def _rule_target(rules, code, day):
    return next((r.daily_target for r in rules if r.stage_code == code and r.effective_from <= day
                 and (r.effective_to is None or day < r.effective_to)), None)


def team_stage_overview(manager, page, page_size, cohort_id=None, lead_id=None, stage_code=None):
    if manager.project is None or manager.project.name != "CODING":
        return dict(items=[], page=page, page_size=page_size, total=0, total_pages=0)
    query = coding_users_query().filter(User.id.in_(manager_team_user_ids(manager.id))).options(
        joinedload(User.role).joinedload(Role.role_type), joinedload(User.reports_to))
    if lead_id == 0:
        query = query.filter(RoleType.code == "employee", User.reports_to_id.is_(None))
    elif lead_id is not None:
        lead = query.filter(User.id == lead_id, RoleType.code == "lead", User.reports_to_id == manager.id).first()
        if lead is None:
            abort(400, message="leadId must reference a lead in your team.")
        query = query.filter(db.or_(User.id == lead_id, User.reports_to_id == lead_id))
    if cohort_id and db.session.get(Cohort, cohort_id) is None:
        abort(400, message="cohortId must reference an existing cohort.")
    users = query.order_by(User.first_name, User.last_name, User.id).all()
    ids = [u.id for u in users]
    memberships = {m.user_id: m for m in CohortMembership.query.options(joinedload(CohortMembership.cohort)).filter(CohortMembership.user_id.in_(ids))}
    evidence = {e.user_id: e for e in UserStageEvidence.query.filter(UserStageEvidence.user_id.in_(ids))}
    by_user = {}
    for p in UserStagePeriod.query.filter(UserStagePeriod.user_id.in_(ids)).order_by(UserStagePeriod.start_date):
        by_user.setdefault(p.user_id, []).append(dict(stage_code=p.stage_code, start_date=p.start_date,
                                                     end_date=p.end_date, source=p.source))
    main_rules, foundation_rules = StageTargetRule.query.all(), FoundationTargetRule.query.all()
    today = business_today()
    items = []
    for user in users:
        membership, ev = memberships.get(user.id), evidence.get(user.id)
        if (cohort_id == 0 and membership) or (cohort_id and cohort_id > 0 and (not membership or membership.cohort_id != cohort_id)):
            continue
        day = min(today, user.last_working_day) if user.last_working_day else today
        periods = by_user.get(user.id, [])
        current = period_on(periods, day)
        code = current["stage_code"] if current else None
        if (stage_code == "Unassigned" and code is not None) or (stage_code and stage_code != "Unassigned" and code != stage_code):
            continue
        foundation = foundation_progress(ev.first_pvp_completed if ev else None,
                                         ev.first_foundation_completed if ev else None, day, user.last_working_day)
        foundation["daily_target"] = _rule_target(foundation_rules, foundation["current_stage"], day)
        joined = user.join_date or (membership.joined_on if membership else None)
        issue = "First completion precedes joining date; review the source dates." if ev and ev.first_completed and joined and ev.first_completed < joined else None
        lead = user if user.role.role_type.code == "lead" else user.reports_to
        if lead and lead.role.role_type.code != "lead":
            lead = None
        items.append(dict(coder=user, cohort=membership.cohort if membership else None,
                          current_stage=code, daily_target=_rule_target(main_rules, code, day), lead=lead,
                          joined_on=joined, stage_as_of=day, periods=periods, foundation=foundation,
                          first_completed=ev.first_completed if ev else None,
                          refreshed_at=ev.refreshed_at if ev else None, data_issue=issue))
    total = len(items)
    return dict(items=items[(page-1)*page_size:page*page_size], page=page, page_size=page_size,
                total=total, total_pages=(total + page_size-1)//page_size)


def change_foundation_target(actor, stage_code, effective_from=None, daily_target=None, reason=None, apply_from=None):
    from app.cohorts.target_changes import change_target
    return change_target(actor, stage_code, daily_target, foundation=True,
                         effective_from=effective_from, apply_from=apply_from, reason=reason)
