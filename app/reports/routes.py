from flask import g
from flask.views import MethodView
from flask_smorest import abort

from app.auth import require_feature, require_role, require_role_types
from app.extensions import db
from app.kairon.models import KaironChartAnalystAction, KaironChartRecord, KaironUploadBatch
from app.kairon.production import unique_completed_production
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import manual_review_user_ids
from app.reports import bp
from app.reports.schemas import (
    BulkApproveManualDailyRecordsSchema,
    BulkApproveResultEnvelopeSchema,
    BulkRejectManualDailyRecordsSchema,
    BulkRejectResultEnvelopeSchema,
    CodingDashboardEnvelopeSchema,
    CodingDashboardQuerySchema,
    EfficiencyQuerySchema,
    EfficiencySummaryEnvelopeSchema,
    KaironCompletedDailyCountPageEnvelopeSchema,
    KaironCompletedRecordQuerySchema,
    KaironCompletedUserPageEnvelopeSchema,
    KaironCompletedUserQuerySchema,
    KaironLeadTeamRangeEnvelopeSchema,
    KaironManagerTeamRangeEnvelopeSchema,
    KaironRecordPageEnvelopeSchema,
    KaironTeamRecordQuerySchema,
    ManualRecordPageEnvelopeSchema,
    ManualReviewQuerySchema,
    ManualTeamDayEnvelopeSchema,
    ManualTeamDayQuerySchema,
    ManualTeamRangeEnvelopeSchema,
    ManualTeamRangeQuerySchema,
    LeadDashboardEnvelopeSchema,
    LeadDashboardQuerySchema,
    ManagerDashboardQuerySchema,
    ManagerDashboardEnvelopeSchema,
    MonthlyGoalEnvelopeSchema,
    MonthlyGoalQuerySchema,
    PaginationQuerySchema,
    SelfKaironChartQuerySchema,
    SelfManualRecordsQuerySchema,
)
from app.reports.lead_dashboard import get_lead_dashboard
from app.reports.manager_dashboard import get_manager_dashboard
from app.reports.services import (
    _month_window,
    bulk_approve_manual_records,
    bulk_reject_manual_records,
    get_efficiency,
    get_coding_dashboard,
    get_kairon_lead_team_range,
    get_kairon_manager_team_range,
    get_manual_team_day,
    get_manual_team_range,
    resolve_dashboard_window,
    get_monthly_goal,
)
from app.users.hierarchy import lead_employee_user_ids, manager_lead_team_user_ids, manager_team_user_ids
from app.users.models import User

# One feature controls both Kairon and Manual report visibility. Write access
# is narrowed further by role at each action endpoint.
REPORTS_FEATURE = "reports"


def _page(query, args):
    page = args["page"]
    page_size = args["page_size"]
    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


@bp.route("/reports/kairon/completed-counts")
class ReportsKaironCompletedCounts(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(PaginationQuerySchema, location="query")
    @bp.response(200, KaironCompletedDailyCountPageEnvelopeSchema)
    def get(self, args):
        query = (
            db.session.query(
                KaironChartRecord.completed_date.label("date"),
                db.func.count(KaironChartRecord.id).label("count"),
            )
            .join(KaironUploadBatch)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.status == "Completed",
                unique_completed_production(),
                KaironChartRecord.completed_date.isnot(None),
            )
        )
        role_type_code = g.user.role.role_type.code
        if role_type_code in ("lead", "employee"):
            query = query.filter(KaironChartRecord.user_id == g.user.id)
        elif role_type_code == "manager":
            query = query.filter(KaironChartRecord.user_id.in_(manager_team_user_ids(g.user.id)))
        query = query.group_by(KaironChartRecord.completed_date).order_by(KaironChartRecord.completed_date.desc())
        return {
            "status": 200,
            "message": "Completed Kairon chart counts retrieved successfully.",
            "data": _page(query, args),
        }


def _scope_kairon_records_to_viewer(query):
    role_type_code = g.user.role.role_type.code
    if role_type_code in ("lead", "employee"):
        return query.filter(KaironChartRecord.user_id == g.user.id)
    if role_type_code == "manager":
        return query.filter(KaironChartRecord.user_id.in_(manager_team_user_ids(g.user.id)))
    return query


@bp.route("/reports/kairon/completed-users")
class ReportsKaironCompletedUsers(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(KaironCompletedUserQuerySchema, location="query")
    @bp.response(200, KaironCompletedUserPageEnvelopeSchema)
    def get(self, args):
        query = (
            db.session.query(
                User.id.label("user_id"),
                User.first_name.label("first_name"),
                User.last_name.label("last_name"),
                db.func.count(KaironChartRecord.id).label("count"),
            )
            .join(KaironChartRecord, KaironChartRecord.user_id == User.id)
            .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.status == "Completed",
                unique_completed_production(),
                KaironChartRecord.completed_date == args["completed_date"],
            )
        )
        query = _scope_kairon_records_to_viewer(query)
        if args.get("analyst"):
            full_name = db.func.concat(User.first_name, " ", User.last_name)
            query = query.filter(full_name.ilike(f"%{args['analyst'].strip()}%"))
        query = query.group_by(User.id, User.first_name, User.last_name).order_by(
            User.first_name.asc(), User.last_name.asc(), User.id.asc()
        )
        return {
            "status": 200,
            "message": "Completed Kairon charts grouped by user retrieved successfully.",
            "data": _page(query, args),
        }


@bp.route("/reports/kairon/completed-records")
class ReportsKaironCompletedRecords(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(KaironCompletedRecordQuerySchema, location="query")
    @bp.response(200, KaironRecordPageEnvelopeSchema)
    def get(self, args):
        query = KaironChartRecord.query.join(KaironUploadBatch).filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "Completed",
            unique_completed_production(),
            KaironChartRecord.completed_date == args["completed_date"],
            KaironChartRecord.user_id == args["user_id"],
        )
        query = _scope_kairon_records_to_viewer(query)
        query = query.order_by(KaironChartRecord.id.asc())
        return {
            "status": 200,
            "message": "Completed Kairon chart details retrieved successfully.",
            "data": _page(query, args),
        }


@bp.route("/reports/kairon/team-range")
class ReportsKaironTeamRange(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role("lead")
    @bp.arguments(ManualTeamRangeQuerySchema, location="query")
    @bp.response(200, KaironLeadTeamRangeEnvelopeSchema)
    def get(self, args):
        return {
            "status": 200,
            "message": "Kairon lead and coder production retrieved successfully.",
            "data": get_kairon_lead_team_range(g.user, args["from_date"], args["to_date"]),
        }


@bp.route("/reports/kairon/manager-team-range")
class ReportsKaironManagerTeamRange(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role("manager")
    @bp.arguments(ManualTeamRangeQuerySchema, location="query")
    @bp.response(200, KaironManagerTeamRangeEnvelopeSchema)
    def get(self, args):
        return {
            "status": 200,
            "message": "Manager Kairon team production retrieved successfully.",
            "data": get_kairon_manager_team_range(g.user, args["from_date"], args["to_date"]),
        }


@bp.route("/reports/kairon/team-holds")
class ReportsKaironTeamHolds(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role("lead")
    @bp.arguments(PaginationQuerySchema, location="query")
    @bp.response(200, KaironRecordPageEnvelopeSchema)
    def get(self, args):
        team_ids = [g.user.id, *lead_employee_user_ids(g.user.id)]
        query = (
            KaironChartRecord.query.join(KaironUploadBatch)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.status == "On Hold",
                KaironChartRecord.user_id.in_(team_ids),
            )
            .order_by(KaironChartRecord.created_date.desc(), KaironChartRecord.id.desc())
        )
        return {
            "status": 200,
            "message": "Current Kairon charts on hold retrieved successfully.",
            "data": _page(query, args),
        }


@bp.route("/reports/kairon/team-records")
class ReportsKaironTeamRecords(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role_types("lead", "manager")
    @bp.arguments(KaironTeamRecordQuerySchema, location="query")
    @bp.response(200, KaironRecordPageEnvelopeSchema)
    def get(self, args):
        if g.user.role.role_type.code == "manager":
            member_ids = manager_team_user_ids(g.user.id)
        else:
            member_ids = [g.user.id, *lead_employee_user_ids(g.user.id)]
        if args["user_id"] not in member_ids:
            abort(404, message="Team member not found.")
        query = (
            KaironChartRecord.query.join(KaironUploadBatch)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.status == "Completed",
                unique_completed_production(),
                KaironChartRecord.user_id == args["user_id"],
                KaironChartRecord.completed_date >= args["from_date"],
                KaironChartRecord.completed_date <= args["to_date"],
            )
            .order_by(KaironChartRecord.completed_date.desc(), KaironChartRecord.id.desc())
        )
        return {
            "status": 200,
            "message": "Team member Kairon chart records retrieved successfully.",
            "data": _page(query, args),
        }


@bp.route("/reports/kairon/records/<int:record_id>")
class ReportsKaironRecordDetail(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    def delete(self, record_id):
        record = (
            KaironChartRecord.query.join(KaironUploadBatch)
            .filter(
                KaironChartRecord.id == record_id,
                KaironUploadBatch.superseded_at.is_(None),
            )
            .first()
        )
        if record is None or record.user_id not in manager_team_user_ids(g.user.id):
            abort(404, message="Kairon chart record not found in your team.")

        KaironChartAnalystAction.query.filter_by(chart_record_id=record.id).delete(
            synchronize_session=False
        )
        db.session.delete(record)
        db.session.commit()
        return {
            "status": 200,
            "message": "Kairon chart record deleted.",
            "data": {"id": record_id},
        }


@bp.route("/reports/kairon")
class ReportsKairon(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(SelfKaironChartQuerySchema, location="query")
    @bp.response(200, KaironRecordPageEnvelopeSchema)
    def get(self, args):
        # Always "and it's mine" - see SelfKaironChartQuerySchema's note on
        # why there's no user_id/user_ids param here to widen this with.
        query = KaironChartRecord.query.join(KaironUploadBatch).filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.user_id == g.user.id,
        )
        if args.get("status"):
            query = query.filter(KaironChartRecord.status == args["status"])
        if args.get("level"):
            query = query.filter(KaironChartRecord.level == args["level"])
        if args.get("as_of_date"):
            query = query.filter(KaironUploadBatch.as_of_date == args["as_of_date"])
        query = query.order_by(KaironChartRecord.created_date.desc(), KaironChartRecord.id.desc())
        return {"status": 200, "message": "Your Kairon chart records retrieved successfully.", "data": _page(query, args)}


@bp.route("/reports/manual")
class ReportsManual(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(SelfManualRecordsQuerySchema, location="query")
    @bp.response(200, ManualRecordPageEnvelopeSchema)
    def get(self, args):
        # Own records only, every status - the point of this tab is
        # seeing where all of your own submissions currently stand (§3.2).
        query = (
            ManualDailyRecord.query.filter_by(user_id=g.user.id)
            .order_by(ManualDailyRecord.record_date.desc(), ManualDailyRecord.id.desc())
        )
        return {"status": 200, "message": "Your manual daily records retrieved successfully.", "data": _page(query, args)}


@bp.route("/reports/manual/reviews")
class ReportsManualReviews(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role_types("manager", "lead")
    @bp.arguments(ManualReviewQuerySchema, location="query")
    @bp.response(200, ManualRecordPageEnvelopeSchema)
    def get(self, args):
        # Leads review direct employees; managers read their team for oversight.
        query = ManualDailyRecord.query.filter(ManualDailyRecord.user_id != g.user.id)
        role_type_code = g.user.role.role_type.code
        if role_type_code == "manager":
            # A manager's Manual screen is their team report, never a
            # system-wide view that leaks another manager's people.
            query = query.filter(ManualDailyRecord.user_id.in_(manager_team_user_ids(g.user.id)))
            if args.get("lead_id") is not None:
                lead_team_ids = manager_lead_team_user_ids(g.user.id, args["lead_id"])
                if lead_team_ids is None:
                    abort(400, message="leadId must reference an active lead in your team.")
                query = query.filter(ManualDailyRecord.user_id.in_(lead_team_ids))
        else:
            query = query.filter(ManualDailyRecord.user_id.in_(manual_review_user_ids(g.user)))
            if args.get("lead_id") is not None and args["lead_id"] != g.user.id:
                abort(400, message="leadId must reference your own team.")
        if args.get("from_date"):
            query = query.filter(ManualDailyRecord.record_date >= args["from_date"])
        if args.get("to_date"):
            query = query.filter(ManualDailyRecord.record_date <= args["to_date"])
        if args.get("user_id"):
            query = query.filter(ManualDailyRecord.user_id == args["user_id"])
        if args.get("user_ids"):
            query = query.filter(ManualDailyRecord.user_id.in_(args["user_ids"]))
        if args.get("exclude_user_ids"):
            query = query.filter(ManualDailyRecord.user_id.notin_(args["exclude_user_ids"]))
        if args.get("status"):
            query = query.filter(ManualDailyRecord.status == args["status"])
        query = query.order_by(ManualDailyRecord.record_date.desc(), ManualDailyRecord.id.desc())
        return {"status": 200, "message": "Manual daily records for review retrieved successfully.", "data": _page(query, args)}


@bp.route("/reports/manual/team-day")
class ReportsManualTeamDay(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role_types("manager", "lead")
    @bp.arguments(ManualTeamDayQuerySchema, location="query")
    @bp.response(200, ManualTeamDayEnvelopeSchema)
    def get(self, args):
        return {
            "status": 200,
            "message": "Manual daily team records retrieved successfully.",
            "data": get_manual_team_day(g.user, args["date"]),
        }


@bp.route("/reports/manual/team-range")
class ReportsManualTeamRange(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role_types("manager", "lead")
    @bp.arguments(ManualTeamRangeQuerySchema, location="query")
    @bp.response(200, ManualTeamRangeEnvelopeSchema)
    def get(self, args):
        return {
            "status": 200,
            "message": "Manual team records retrieved successfully.",
            "data": get_manual_team_range(g.user, args["from_date"], args["to_date"]),
        }


@bp.route("/reports/manual/reviews/bulk-approve")
class ReportsManualReviewsBulkApprove(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("lead")
    @bp.arguments(BulkApproveManualDailyRecordsSchema)
    @bp.response(200, BulkApproveResultEnvelopeSchema)
    def post(self, data):
        result = bulk_approve_manual_records(data["ids"], g.user.id)
        return {"status": 200, "message": "Manual daily records approved.", "data": result}


@bp.route("/reports/manual/reviews/bulk-reject")
class ReportsManualReviewsBulkReject(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("lead")
    @bp.arguments(BulkRejectManualDailyRecordsSchema)
    @bp.response(200, BulkRejectResultEnvelopeSchema)
    def post(self, data):
        result = bulk_reject_manual_records(data["items"], g.user.id)
        return {"status": 200, "message": "Manual daily records rejected.", "data": result}


@bp.route("/dashboards/coding")
class CodingDashboard(MethodView):
    @require_feature("dashboard")
    @require_role_types("super_admin", "admin", "manager")
    @bp.arguments(CodingDashboardQuerySchema, location="query")
    @bp.response(200, CodingDashboardEnvelopeSchema)
    def get(self, args):
        from_date, to_date = resolve_dashboard_window(args)
        if from_date > to_date:
            abort(400, message="from must be on or before to.")
        cards = get_coding_dashboard(
            from_date,
            to_date,
            program=args.get("program"),
            lead_id=args.get("lead_id"),
            cohort_id=args.get("cohort_id"),
            include_daily=args.get("include_daily", False),
        )
        return {"status": 200, "message": "Coding project dashboard retrieved successfully.", "data": cards}


@bp.route("/dashboards/my-efficiency")
class MyEfficiencyDashboard(MethodView):
    @require_feature("dashboard")
    @bp.arguments(EfficiencyQuerySchema, location="query")
    @bp.response(200, EfficiencySummaryEnvelopeSchema)
    def get(self, args):
        from_date, to_date = resolve_dashboard_window(args)
        if from_date > to_date:
            abort(400, message="from must be on or before to.")
        efficiency = get_efficiency([g.user.id], from_date, to_date, include_daily=True)[g.user.id]
        return {"status": 200, "message": "Your efficiency retrieved successfully.", "data": efficiency}


@bp.route("/dashboards/monthly-goal")
class MonthlyGoalDashboard(MethodView):
    @require_feature("dashboard")
    @require_role_types("lead", "employee")
    @bp.arguments(MonthlyGoalQuerySchema, location="query")
    @bp.response(200, MonthlyGoalEnvelopeSchema)
    def get(self, args):
        result = get_monthly_goal(g.user, args.get("month"))
        return {"status": 200, "message": "Monthly chart goal retrieved successfully.", "data": result}


@bp.route("/dashboards/lead")
class LeadDashboard(MethodView):
    @require_feature("dashboard")
    @require_role_types("lead")
    @bp.arguments(LeadDashboardQuerySchema, location="query")
    @bp.response(200, LeadDashboardEnvelopeSchema)
    def get(self, args):
        from_date, to_date = resolve_dashboard_window(args)
        if from_date > to_date:
            abort(400, message="from must be on or before to.")
        # Monthly goals stay full-month, just as in the coder dashboard.
        # Other period modes use the exact selected reporting window.
        goal_to_date = to_date
        if not any(args.get(key) for key in ("from_date", "to_date", "date", "year")):
            _, goal_to_date = _month_window(args.get("month"))
        result = get_lead_dashboard(g.user, from_date, to_date, args.get("coder_id"), goal_to_date)
        return {"status": 200, "message": "QA and coder performance retrieved successfully.", "data": result}


@bp.route("/dashboards/manager")
class ManagerDashboard(MethodView):
    @require_feature("dashboard")
    @require_role_types("manager")
    @bp.arguments(ManagerDashboardQuerySchema, location="query")
    @bp.response(200, ManagerDashboardEnvelopeSchema)
    def get(self, args):
        from_date, to_date = resolve_dashboard_window(args)
        if from_date > to_date:
            abort(400, message="from must be on or before to.")
        goal_to_date = to_date
        if not any(args.get(key) for key in ("from_date", "to_date", "date", "year")):
            _, goal_to_date = _month_window(args.get("month"))
        result = get_manager_dashboard(g.user, from_date, to_date,
            lead_id=args.get("lead_id"), coder_id=args.get("coder_id"),
            cohort_id=args.get("cohort_id"), program=args.get("program"), goal_to_date=goal_to_date)
        return {"status": 200, "message": "Manager performance retrieved successfully.", "data": result}
