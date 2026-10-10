from datetime import date, datetime, timezone

from flask import g
from flask.views import MethodView
from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.auth import require_feature, require_role
from app.cohorts import bp
from app.cohorts.models import Cohort, CohortJoinReview, Coder, CoderStagePeriod, Stage, StageException, StageTargetRule
from app.cohorts.schemas import (
    CoderEnvelopeSchema,
    CoderListEnvelopeSchema,
    CoderSchema,
    CoderStagePeriodListEnvelopeSchema,
    CodingUserSummaryListEnvelopeSchema,
    ChangeStageTargetSchema,
    ChangeFoundationTargetSchema,
    CohortDetailEnvelopeSchema,
    CohortEnvelopeSchema,
    CohortJoinReviewEnvelopeSchema,
    CohortJoinReviewListEnvelopeSchema,
    CohortJoinReviewQuerySchema,
    CohortListEnvelopeSchema,
    CohortSchema,
    DailyTargetEnvelopeSchema,
    DailyTargetQuerySchema,
    ResolveCohortJoinReviewSchema,
    StageExceptionEnvelopeSchema,
    StageExceptionListEnvelopeSchema,
    StageExceptionSchema,
    StageListEnvelopeSchema,
    StageTargetRuleEnvelopeSchema,
    StageTargetRuleListEnvelopeSchema,
    StageTargetRuleQuerySchema,
    StageTargetRuleSchema,
    TeamCoderOverviewEnvelopeSchema,
    TeamCoderOverviewQuerySchema,
    TeamCohortCreateSchema,
    TeamCohortEnvelopeSchema,
    TeamCohortListEnvelopeSchema,
)
from app.cohorts.services import (
    assign_cohort,
    change_stage_target,
    compute_stage_periods,
    create_user_cohort,
    current_stage_period,
    eligible_coding_users_for_manager,
    get_team_coder_overview,
    overlapping_rule_exists,
    target_for_period,
)
from app.extensions import db
from app.responses import MessageEnvelopeSchema

OVERLAP_MESSAGE = "This date range overlaps an existing rule for this stage."


@bp.route("/team/coders")
class TeamCoderOverview(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(TeamCoderOverviewQuerySchema, location="query")
    @bp.response(200, TeamCoderOverviewEnvelopeSchema)
    def get(self, args):
        page = get_team_coder_overview(
            g.user,
            page=args["page"],
            page_size=args["page_size"],
            cohort_id=args.get("cohort_id"),
            lead_id=args.get("lead_id"),
            stage_code=args.get("stage_code"),
        )
        return {"status": 200, "message": "Team coder overview retrieved successfully.", "data": page}


@bp.route("/team/cohorts")
class TeamCohorts(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.response(200, TeamCohortListEnvelopeSchema)
    def get(self):
        cohorts = Cohort.query.order_by(Cohort.sequence_no.desc()).all()
        return {"status": 200, "message": "Cohorts retrieved successfully.", "data": cohorts}

    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(TeamCohortCreateSchema)
    @bp.response(201, TeamCohortEnvelopeSchema)
    def post(self, data):
        cohort = create_user_cohort(
            g.user,
            label=data["label"],
            window_start=data["window_start"],
            window_end=data.get("window_end"),
            member_ids=data["member_ids"],
        )
        return {"status": 201, "message": "Cohort created successfully.", "data": cohort}


@bp.route("/team/cohorts/eligible-members")
class TeamCohortEligibleMembers(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.response(200, CodingUserSummaryListEnvelopeSchema)
    def get(self):
        assigned_ids = {m.user_id for c in Cohort.query.all() for m in c.user_memberships}
        users = [u for u in eligible_coding_users_for_manager(g.user) if u.id not in assigned_ids]
        return {"status": 200, "message": "Eligible cohort members retrieved successfully.", "data": users}


@bp.route("/team/stage-targets/change")
class TeamStageTargetChange(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(ChangeStageTargetSchema)
    @bp.response(201, StageTargetRuleEnvelopeSchema)
    def post(self, data):
        rule = change_stage_target(g.user, **data)
        return {"status": 201, "message": "Stage target changed successfully.", "data": rule}


@bp.route("/stages")
class Stages(MethodView):
    @bp.response(200, StageListEnvelopeSchema)
    def get(self):
        stages = Stage.query.order_by(Stage.sort_order).all()
        return {"status": 200, "message": "Stages retrieved successfully.", "data": stages}


@bp.route("/cohorts")
class Cohorts(MethodView):
    @bp.response(200, CohortListEnvelopeSchema)
    def get(self):
        cohorts = Cohort.query.order_by(Cohort.sequence_no).all()
        return {"status": 200, "message": "Cohorts retrieved successfully.", "data": cohorts}

    @require_role("super_admin")
    @bp.arguments(CohortSchema)
    @bp.response(201, CohortEnvelopeSchema)
    def post(self, data):
        # Cohorts are opened explicitly by an ops action, never auto-created
        # from a date-gap heuristic - sequence_no is strictly increasing and
        # never reused or reordered.
        sequence_no = (db.session.query(db.func.max(Cohort.sequence_no)).scalar() or 0) + 1
        cohort = Cohort(
            sequence_no=sequence_no,
            label=data.get("label") or f"Cohort {sequence_no}",
            window_start=data["window_start"],
            window_end=data.get("window_end"),
        )
        db.session.add(cohort)
        db.session.commit()
        return {"status": 201, "message": "Cohort opened successfully.", "data": cohort}


@bp.route("/cohorts/<int:cohort_id>")
class CohortDetail(MethodView):
    @bp.response(200, CohortDetailEnvelopeSchema)
    def get(self, cohort_id):
        cohort = Cohort.query.get_or_404(cohort_id)
        return {"status": 200, "message": "Cohort retrieved successfully.", "data": cohort}

    @require_role("super_admin")
    @bp.arguments(CohortSchema(partial=True))
    @bp.response(200, CohortEnvelopeSchema)
    def patch(self, data, cohort_id):
        cohort = Cohort.query.get_or_404(cohort_id)
        # sequence_no is fixed at creation; only label and window_end are
        # ever meant to change here (closure policy for window_start is
        # still an open decision per the functionality doc).
        if "label" in data and data["label"]:
            cohort.label = data["label"]
        if "window_end" in data:
            cohort.window_end = data["window_end"]
        db.session.commit()
        return {"status": 200, "message": "Cohort updated successfully.", "data": cohort}


@bp.route("/cohort-join-review")
class CohortJoinReviews(MethodView):
    @require_role("super_admin")
    @bp.arguments(CohortJoinReviewQuerySchema, location="query")
    @bp.response(200, CohortJoinReviewListEnvelopeSchema)
    def get(self, args):
        query = CohortJoinReview.query
        if args.get("status"):
            query = query.filter_by(status=args["status"])
        reviews = query.order_by(CohortJoinReview.created_at).all()
        return {"status": 200, "message": "Cohort join reviews retrieved successfully.", "data": reviews}


@bp.route("/cohort-join-review/<int:review_id>/resolve")
class ResolveCohortJoinReview(MethodView):
    @require_role("super_admin")
    @bp.arguments(ResolveCohortJoinReviewSchema)
    @bp.response(200, CohortJoinReviewEnvelopeSchema)
    def post(self, data, review_id):
        review = CohortJoinReview.query.get_or_404(review_id)
        if review.status != "pending":
            abort(409, message="This review has already been resolved.")

        cohort = db.session.get(Cohort, data["cohort_id"])
        if cohort is None:
            abort(400, message="cohort_id must reference an existing cohort.")

        coder = db.session.get(Coder, review.coder_id)
        coder.cohort_id = cohort.id
        review.status = "resolved"
        review.resolved_cohort_id = cohort.id
        review.resolved_by_id = g.user.id
        review.resolved_at = datetime.now(timezone.utc)
        db.session.commit()
        return {"status": 200, "message": "Cohort join review resolved successfully.", "data": review}


@bp.route("/stage-target-rules")
class StageTargetRules(MethodView):
    @bp.arguments(StageTargetRuleQuerySchema, location="query")
    @bp.response(200, StageTargetRuleListEnvelopeSchema)
    def get(self, args):
        query = StageTargetRule.query
        if args.get("stage"):
            query = query.filter_by(stage_code=args["stage"])
        rules = query.order_by(StageTargetRule.stage_code, StageTargetRule.effective_from).all()
        return {"status": 200, "message": "Stage target rules retrieved successfully.", "data": rules}

    @require_role("super_admin")
    @bp.arguments(StageTargetRuleSchema)
    @bp.response(201, StageTargetRuleEnvelopeSchema)
    def post(self, data):
        if overlapping_rule_exists(data["stage_code"], data["effective_from"], data.get("effective_to")):
            abort(409, message=OVERLAP_MESSAGE)

        rule = StageTargetRule(
            stage_code=data["stage_code"],
            effective_from=data["effective_from"],
            effective_to=data.get("effective_to"),
            daily_target=data["daily_target"],
            created_by_id=g.user.id,
        )
        db.session.add(rule)
        try:
            db.session.commit()
        except IntegrityError:
            # backstop for a race between the pre-check above and this write -
            # the GiST exclusion constraint is the real source of truth.
            db.session.rollback()
            abort(409, message=OVERLAP_MESSAGE)
        return {"status": 201, "message": "Stage target rule created successfully.", "data": rule}


@bp.route("/stage-target-rules/<int:rule_id>")
class StageTargetRuleDetail(MethodView):
    @require_role("super_admin")
    @bp.arguments(StageTargetRuleSchema(partial=True))
    @bp.response(200, StageTargetRuleEnvelopeSchema)
    def patch(self, data, rule_id):
        rule = StageTargetRule.query.get_or_404(rule_id)

        stage_code = data.get("stage_code", rule.stage_code)
        effective_from = data.get("effective_from", rule.effective_from)
        effective_to = data["effective_to"] if "effective_to" in data else rule.effective_to
        if effective_to is not None and effective_to <= effective_from:
            abort(400, message="effective_to must be after effective_from.")

        if overlapping_rule_exists(stage_code, effective_from, effective_to, exclude_id=rule.id):
            abort(409, message=OVERLAP_MESSAGE)

        for key in ("stage_code", "effective_from", "effective_to", "daily_target"):
            if key in data:
                setattr(rule, key, data[key])
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message=OVERLAP_MESSAGE)
        return {"status": 200, "message": "Stage target rule updated successfully.", "data": rule}

    @require_role("super_admin")
    @bp.response(200, MessageEnvelopeSchema)
    def delete(self, rule_id):
        rule = StageTargetRule.query.get_or_404(rule_id)
        if rule.effective_from <= date.today():
            abort(409, message="Only a rule whose effective_from is still in the future can be deleted.")
        db.session.delete(rule)
        db.session.commit()
        return {"status": 200, "message": "Stage target rule deleted successfully.", "data": None}


@bp.route("/coders")
class Coders(MethodView):
    @bp.response(200, CoderListEnvelopeSchema)
    def get(self):
        coders = Coder.query.order_by(Coder.full_name).all()
        return {"status": 200, "message": "Coders retrieved successfully.", "data": coders}

    @require_role("super_admin")
    @bp.arguments(CoderSchema)
    @bp.response(201, CoderEnvelopeSchema)
    def post(self, data):
        coder = Coder(full_name=data["full_name"], join_date=data["join_date"])
        db.session.add(coder)
        try:
            db.session.flush()  # assigns coder.id, needed below; surfaces a duplicate name
        except IntegrityError:
            db.session.rollback()
            abort(409, message="A coder with this name already exists.")

        assign_cohort(coder)
        compute_stage_periods(coder)
        return {"status": 201, "message": "Coder created successfully.", "data": coder}


@bp.route("/coders/<int:coder_id>")
class CoderDetail(MethodView):
    @bp.response(200, CoderEnvelopeSchema)
    def get(self, coder_id):
        coder = Coder.query.get_or_404(coder_id)
        return {"status": 200, "message": "Coder retrieved successfully.", "data": coder}


@bp.route("/coders/<int:coder_id>/stage-periods")
class CoderStagePeriods(MethodView):
    @bp.response(200, CoderStagePeriodListEnvelopeSchema)
    def get(self, coder_id):
        coder = Coder.query.get_or_404(coder_id)
        periods = (
            CoderStagePeriod.query.filter_by(coder_id=coder.id)
            .join(Stage, Stage.code == CoderStagePeriod.stage_code)
            .order_by(Stage.sort_order)
            .all()
        )
        return {"status": 200, "message": "Stage periods retrieved successfully.", "data": periods}


@bp.route("/coders/<int:coder_id>/daily-target")
class CoderDailyTarget(MethodView):
    @bp.arguments(DailyTargetQuerySchema, location="query")
    @bp.response(200, DailyTargetEnvelopeSchema)
    def get(self, args, coder_id):
        coder = Coder.query.get_or_404(coder_id)
        on_date = args["date"]
        period = current_stage_period(coder, on_date)
        target = target_for_period(period, on_date)
        return {
            "status": 200,
            "message": "Daily target retrieved successfully.",
            "data": {
                "date": on_date,
                "stage_code": period.stage_code if period else None,
                "daily_target": target,
            },
        }


@bp.route("/coders/<int:coder_id>/stage-exceptions")
class CoderStageExceptions(MethodView):
    @bp.response(200, StageExceptionListEnvelopeSchema)
    def get(self, coder_id):
        coder = Coder.query.get_or_404(coder_id)
        exceptions = (
            StageException.query.filter_by(coder_id=coder.id).order_by(StageException.created_at).all()
        )
        return {"status": 200, "message": "Stage exceptions retrieved successfully.", "data": exceptions}

    @require_role("super_admin")
    @bp.arguments(StageExceptionSchema)
    @bp.response(201, StageExceptionEnvelopeSchema)
    def post(self, data, coder_id):
        coder = Coder.query.get_or_404(coder_id)
        exception = StageException(
            coder_id=coder.id,
            stage_code=data["stage_code"],
            extra_days=data["extra_days"],
            reason=data["reason"],
            created_by_id=g.user.id,
        )
        db.session.add(exception)
        db.session.commit()

        compute_stage_periods(coder)  # this coder's downstream periods only
        return {"status": 201, "message": "Stage exception recorded successfully.", "data": exception}


@bp.route("/team/foundation-target-rules")
class FoundationTargets(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.response(200, StageTargetRuleListEnvelopeSchema)
    def get(self):
        from app.cohorts.models import FoundationTargetRule
        if g.user.project is None or g.user.project.name != "CODING":
            abort(403, message="Only a CODING manager may view Foundation targets.")
        rules = FoundationTargetRule.query.order_by(FoundationTargetRule.stage_code, FoundationTargetRule.effective_from).all()
        return {"status": 200, "message": "Foundation targets retrieved successfully.", "data": rules}


@bp.route("/team/foundation-targets/change")
class FoundationTargetChange(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(ChangeFoundationTargetSchema)
    @bp.response(201, StageTargetRuleEnvelopeSchema)
    def post(self, data):
        from app.cohorts.stage_refresh import change_foundation_target
        rule = change_foundation_target(g.user, **data)
        return {"status": 201, "message": "Foundation target changed successfully.", "data": rule}
