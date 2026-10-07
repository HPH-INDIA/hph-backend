from flask import g
from flask.views import MethodView

from app.auth import require_feature
from app.login_hours import bp
from app.login_hours.models import LoginHoursUploadBatch
from app.login_hours.schemas import (
    LoginHourRecordPageEnvelopeSchema,
    LoginHourRecordQuerySchema,
    LoginHoursUploadBatchEnvelopeSchema,
    LoginHoursUploadBatchListEnvelopeSchema,
    LoginHoursUploadRequestSchema,
    LoginHoursRecordChunkSchema,
)
from app.login_hours.services import import_login_hours, import_login_hour_chunk, list_login_hour_records


@bp.route("/login-hours/uploads")
class LoginHoursUploads(MethodView):
    @require_feature("login_hours")
    @bp.response(200, LoginHoursUploadBatchListEnvelopeSchema)
    def get(self):
        query = LoginHoursUploadBatch.query
        if g.user.role.role_type.code not in {"admin", "super_admin"}:
            query = query.filter_by(uploaded_by_id=g.user.id)
        batches = query.order_by(LoginHoursUploadBatch.uploaded_at.desc()).all()
        return {"status": 200, "message": "Login-hours uploads retrieved successfully.", "data": batches}

    @require_feature("login_hours", access="write")
    @bp.arguments(LoginHoursUploadRequestSchema)
    @bp.response(201, LoginHoursUploadBatchEnvelopeSchema)
    def post(self, data):
        batch = import_login_hours(data["file_base64"], data["source_filename"], g.user.id)
        return {"status": 201, "message": "Login-hours workbook imported successfully.", "data": batch}


@bp.route("/login-hours/records")
class LoginHoursRecords(MethodView):
    @require_feature("login_hours")
    @bp.arguments(LoginHourRecordQuerySchema, location="query")
    @bp.response(200, LoginHourRecordPageEnvelopeSchema)
    def get(self, args):
        page = list_login_hour_records(
            g.user,
            page=args["page"],
            page_size=args["page_size"],
            from_date=args.get("from_date"),
            to_date=args.get("to_date"),
            user_id=args.get("user_id"),
            lead_id=args.get("lead_id"),
            cohort_id=args.get("cohort_id"),
            user_ids=args.get("user_ids"),
            project_id=args.get("project_id"),
        )
        return {"status": 200, "message": "Login-hour records retrieved successfully.", "data": page}


@bp.route("/login-hours/uploads/records")
class LoginHoursRecordChunks(MethodView):
    @require_feature("login_hours", access="write")
    @bp.arguments(LoginHoursRecordChunkSchema)
    @bp.response(201, LoginHoursUploadBatchEnvelopeSchema)
    def post(self, data):
        batch = import_login_hour_chunk(data, g.user.id)
        return {"status": 201, "message": "Attendance records saved successfully.", "data": batch}
