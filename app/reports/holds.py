"""Current hold inventory, always restricted to the viewer's reporting tree."""

from flask_smorest import abort
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.roles.models import Role
from app.users.hierarchy import lead_employee_user_ids, manager_team_user_ids
from app.users.models import User


HOLD_AGE_BUCKETS = ("on_track", "monitor", "attention", "priority", "unknown")
HOLD_SORT_COLUMNS = ("id", "user", "program", "created", "practice", "lastAction", "age", "tat")


def hold_age_bucket():
    """Use the imported Kairon age; missing/invalid ages must never look healthy."""
    age = KaironChartRecord.age_days
    return db.case(
        (age.between(0, 10), "on_track"),
        (age.between(11, 20), "monitor"),
        (age.between(21, 30), "attention"),
        (age > 30, "priority"),
        else_="unknown",
    )


def order_holds(query, args):
    """Sort the entire authorized result before pagination, with stable ties."""
    sort_by = args.get("sort_by", "created")
    if sort_by == "user":
        query = query.join(User, User.id == KaironChartRecord.user_id)
    columns = {
        "id": [KaironChartRecord.id],
        "user": [db.func.lower(db.func.trim(User.first_name + " " + User.last_name))],
        "program": [db.func.lower(KaironChartRecord.program), KaironChartRecord.level],
        "created": [KaironChartRecord.created_date],
        "practice": [db.func.lower(db.func.nullif(KaironChartRecord.practice, ""))],
        "lastAction": [db.func.lower(db.func.nullif(KaironChartRecord.last_action, "")), KaironChartRecord.actions],
        "age": [KaironChartRecord.age_days],
        "tat": [KaironChartRecord.tat_days],
    }[sort_by]
    descending = args.get("sort_direction", "desc") == "desc"
    order = [(column.desc() if descending else column.asc()).nullslast() for column in columns]
    return query.order_by(*order, KaironChartRecord.id.desc())


def hold_members(viewer):
    role = viewer.role.role_type.code
    if role == "employee":
        ids = [viewer.id]
    elif role == "lead":
        ids = [viewer.id, *lead_employee_user_ids(viewer.id)]
    elif role == "manager":
        ids = manager_team_user_ids(viewer.id)
    else:
        abort(403, message="Chart holds require a coder, lead, or manager role.")
    return (
        User.query.options(joinedload(User.role).joinedload(Role.role_type))
        .filter(User.id.in_(ids))
        .order_by(User.first_name, User.last_name, User.id)
        .all()
    )


def member_lead_id(member, lead_ids):
    if member.role.role_type.code == "lead":
        return member.id
    return member.reports_to_id if member.reports_to_id in lead_ids else None


def hold_query(members, args):
    """Every optional filter narrows the authorized set; none replaces it."""
    member_ids = {member.id for member in members}
    lead_ids = {member.id for member in members if member.role.role_type.code == "lead"}
    user_id = args.get("user_id")
    lead_id = args.get("lead_id")
    if user_id is not None and user_id not in member_ids:
        abort(404, message="User not found in your team.")
    if lead_id and lead_id != "unassigned" and int(lead_id) not in lead_ids:
        abort(404, message="Lead not found in your team.")

    selected = members
    view = args.get("view", "all")
    if view != "all":
        role = "employee" if view == "coders" else "lead"
        selected = [member for member in selected if member.role.role_type.code == role]
    if lead_id:
        selected_lead = None if lead_id == "unassigned" else int(lead_id)
        selected = [member for member in selected if member_lead_id(member, lead_ids) == selected_lead]
    if user_id is not None:
        selected = [member for member in selected if member.id == user_id]

    query = KaironChartRecord.query.join(KaironUploadBatch).filter(
        KaironUploadBatch.superseded_at.is_(None),
        KaironChartRecord.status == "On Hold",
        KaironChartRecord.user_id.in_([member.id for member in selected]),
    )
    if args.get("created_from"):
        query = query.filter(KaironChartRecord.created_date >= args["created_from"])
    if args.get("created_to"):
        query = query.filter(KaironChartRecord.created_date <= args["created_to"])
    if args.get("practice") is not None:
        query = query.filter(KaironChartRecord.practice == args["practice"])
    if args.get("without_practice"):
        query = query.filter(db.or_(KaironChartRecord.practice.is_(None), KaironChartRecord.practice == ""))
    if args.get("age_bucket"):
        query = query.filter(hold_age_bucket() == args["age_bucket"])
    return query


def hold_summary(members, args):
    # Age groups respect every other filter and stay visible when one is selected.
    bucket = hold_age_bucket()
    grouped = (hold_query(members, {**args, "age_bucket": None})
               .with_entities(KaironChartRecord.user_id, bucket, db.func.count(KaironChartRecord.id))
               .group_by(KaironChartRecord.user_id, bucket).all())
    counts = {}
    age_buckets = dict.fromkeys(HOLD_AGE_BUCKETS, 0)
    for user_id, age_group, count in grouped:
        age_buckets[age_group] += count
        if not args.get("age_bucket") or args["age_bucket"] == age_group:
            counts[user_id] = counts.get(user_id, 0) + count
    # Keep filter options stable even when the selected filters return no rows.
    practices = [practice for (practice,) in (
        hold_query(members, {})
        .with_entities(KaironChartRecord.practice)
        .filter(KaironChartRecord.practice.isnot(None), KaironChartRecord.practice != "")
        .distinct().order_by(KaironChartRecord.practice).all()
    )]
    lead_ids = {member.id for member in members if member.role.role_type.code == "lead"}
    users = [{
        "id": member.id,
        "name": f"{member.first_name} {member.last_name}".strip(),
        "role_type": member.role.role_type.code,
        "lead_id": member_lead_id(member, lead_ids),
        "count": counts.get(member.id, 0),
    } for member in members]
    return {
        "total": sum(counts.values()),
        "coder_count": sum(user["count"] for user in users if user["role_type"] == "employee"),
        "lead_count": sum(user["count"] for user in users if user["role_type"] == "lead"),
        "age_buckets": age_buckets,
        "users": users,
        "practices": practices,
    }
