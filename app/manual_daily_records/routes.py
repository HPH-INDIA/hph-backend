from flask import Response, g
from flask.views import MethodView
from flask_smorest import abort

from app.auth import require_feature, require_role, require_role_types
from app.extensions import db
from app.manual_daily_records import bp
from app.manual_daily_records.models import ManualDailyRecord, ManualImportBatch
from app.manual_daily_records.schemas import (
    ManualDailyRecordEnvelopeSchema,
    ManualDailyRecordListEnvelopeSchema,
    ManualDailyRecordQuerySchema,
    ManualDailyRecordUpsertSchema,
    ManualBulkUploadEnvelopeSchema,
    ManualBulkUploadRequestSchema,
    ManualImportChunkSchema,
    ManualImportProgressEnvelopeSchema,
    ManualImportStartSchema,
    RejectManualDailyRecordSchema,
)
from app.manual_daily_records.services import (
    approve_record,
    build_manual_upload_template_xlsx,
    complete_manual_import,
    import_manual_daily_records,
    manual_review_user_ids,
    process_manual_import_chunk,
    reject_record,
    scope_manual_records,
    start_manual_import,
    upsert_own_record,
)

# Manual records belong to Reports. Manager/Lead/Employee roles with Reports
# write may submit their own record; leads review their direct employees.
REPORTS_FEATURE = "reports"


@bp.route("/manual-daily-records")
class ManualDailyRecords(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(ManualDailyRecordQuerySchema, location="query")
    @bp.response(200, ManualDailyRecordListEnvelopeSchema)
    def get(self, args):
        query = scope_manual_records(ManualDailyRecord.query, g.user)
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
        records = query.order_by(ManualDailyRecord.record_date.desc()).all()
        return {"status": 200, "message": "Manual daily records retrieved successfully.", "data": records}

    @require_feature(REPORTS_FEATURE, access="write")
    @require_role_types("manager", "lead", "employee")
    @bp.arguments(ManualDailyRecordUpsertSchema)
    @bp.response(200, ManualDailyRecordEnvelopeSchema)
    def post(self, data):
        # Always the caller's own record - there is no manager-entered or
        # on-behalf-of path (§4), so no user id is ever accepted here.
        record = upsert_own_record(g.user.id, data)
        return {"status": 200, "message": "Manual daily record saved successfully.", "data": record}


@bp.route("/manual-daily-records/upload-template")
class ManualDailyRecordsUploadTemplate(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role("manager")
    def get(self):
        return Response(
            build_manual_upload_template_xlsx(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=manual_production_upload_template.xlsx"
            },
        )


@bp.route("/manual-daily-records/bulk-upload")
class ManualDailyRecordsBulkUpload(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(ManualBulkUploadRequestSchema)
    @bp.response(201, ManualBulkUploadEnvelopeSchema)
    def post(self, data):
        result = import_manual_daily_records(
            file_base64=data["file_base64"],
            source_filename=data["source_filename"],
            record_date=data["record_date"],
            uploaded_by_id=g.user.id,
        )
        return {
            "status": 201,
            "message": "Manual production records imported successfully.",
            "data": result,
        }


@bp.route("/manual-daily-records/imports")
class ManualDailyRecordsImports(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(ManualImportStartSchema)
    @bp.response(201, ManualImportProgressEnvelopeSchema)
    def post(self, data):
        batch = start_manual_import(
            source_filename=data["source_filename"],
            file_checksum=data["file_checksum"],
            total_rows=data["total_rows"],
            uploaded_by_id=g.user.id,
        )
        return {"status": 201, "message": "Manual production import started.", "data": batch}


@bp.route("/manual-daily-records/imports/<int:batch_id>")
class ManualDailyRecordsImportProgress(MethodView):
    @require_feature(REPORTS_FEATURE)
    @require_role("manager")
    @bp.response(200, ManualImportProgressEnvelopeSchema)
    def get(self, batch_id):
        batch = db.get_or_404(ManualImportBatch, batch_id)
        if batch.uploaded_by_id != g.user.id:
            abort(403, message="This manual production import belongs to another manager.")
        return {"status": 200, "message": "Manual production import progress retrieved.", "data": batch}


@bp.route("/manual-daily-records/imports/<int:batch_id>/chunks/<int:chunk_number>")
class ManualDailyRecordsImportChunks(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(ManualImportChunkSchema)
    @bp.response(200, ManualImportProgressEnvelopeSchema)
    def post(self, data, batch_id, chunk_number):
        batch, _chunk = process_manual_import_chunk(
            batch_id=batch_id,
            chunk_number=chunk_number,
            checksum=data["checksum"],
            rows=data["rows"],
            uploaded_by_id=g.user.id,
        )
        return {"status": 200, "message": f"Chunk {chunk_number} uploaded.", "data": batch}


@bp.route("/manual-daily-records/imports/<int:batch_id>/complete")
class ManualDailyRecordsImportComplete(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.response(200, ManualImportProgressEnvelopeSchema)
    def post(self, batch_id):
        batch = complete_manual_import(batch_id, g.user.id)
        return {"status": 200, "message": "Manual production import completed.", "data": batch}


@bp.route("/manual-daily-records/<int:record_id>/approve")
class ApproveManualDailyRecord(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("lead")
    @bp.response(200, ManualDailyRecordEnvelopeSchema)
    def post(self, record_id):
        record = ManualDailyRecord.query.filter(
            ManualDailyRecord.id == record_id,
            ManualDailyRecord.user_id.in_(manual_review_user_ids(g.user)),
        ).first_or_404()
        if record.status != "pending":
            abort(409, message="This record isn't pending review.")

        approve_record(record, g.user.id)
        db.session.commit()
        return {"status": 200, "message": "Manual daily record approved successfully.", "data": record}


@bp.route("/manual-daily-records/<int:record_id>/reject")
class RejectManualDailyRecord(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("lead")
    @bp.arguments(RejectManualDailyRecordSchema)
    @bp.response(200, ManualDailyRecordEnvelopeSchema)
    def post(self, data, record_id):
        record = ManualDailyRecord.query.filter(
            ManualDailyRecord.id == record_id,
            ManualDailyRecord.user_id.in_(manual_review_user_ids(g.user)),
        ).first_or_404()
        if record.status != "pending":
            abort(409, message="This record isn't pending review.")

        reject_record(record, g.user.id, data.get("reason"))
        db.session.commit()
        return {"status": 200, "message": "Manual daily record rejected successfully.", "data": record}
