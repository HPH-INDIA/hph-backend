from flask import g
from flask import Response
from flask.views import MethodView

from app.auth import require_feature, require_role
from app.extensions import db
from app.kairon import bp
from app.users.hierarchy import report_team_user_ids
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.kairon.schemas import (
    KaironChartQuerySchema,
    KaironChartRecordListEnvelopeSchema,
    KaironImportChunkSchema,
    KaironImportProgressEnvelopeSchema,
    KaironImportStartSchema,
    KaironUploadBatchEnvelopeSchema,
    KaironUploadBatchListEnvelopeSchema,
    KaironUploadRequestSchema,
)
from app.kairon.services import (
    build_upload_template_csv,
    complete_cumulative_import,
    import_batch,
    process_import_chunk,
    start_cumulative_import,
)

# Kairon belongs to Reports. Reports read grants visibility; Reports write
# plus the Manager role grants bulk-upload access.
REPORTS_FEATURE = "reports"


@bp.route("/kairon/upload-template")
class KaironUploadTemplate(MethodView):
    @require_feature(REPORTS_FEATURE)
    def get(self):
        csv_text = build_upload_template_csv()
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=kairon_upload_template.csv"},
        )


@bp.route("/kairon/uploads")
class KaironUploads(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.response(200, KaironUploadBatchListEnvelopeSchema)
    def get(self):
        batches = KaironUploadBatch.query.order_by(KaironUploadBatch.uploaded_at.desc()).all()
        return {"status": 200, "message": "Kairon upload batches retrieved successfully.", "data": batches}

    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(KaironUploadRequestSchema)
    @bp.response(201, KaironUploadBatchEnvelopeSchema)
    def post(self, data):
        batch = import_batch(
            as_of_date=data["as_of_date"],
            rows=data["rows"],
            uploaded_by_id=g.user.id,
            source_filename=data.get("source_filename"),
        )
        return {"status": 201, "message": "Kairon chart records uploaded successfully.", "data": batch}


@bp.route("/kairon/imports")
class KaironImports(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(KaironImportStartSchema)
    @bp.response(201, KaironImportProgressEnvelopeSchema)
    def post(self, data):
        batch = start_cumulative_import(
            source_filename=data["source_filename"],
            file_checksum=data["file_checksum"],
            total_rows=data["total_rows"],
            uploaded_by_id=g.user.id,
        )
        return {"status": 201, "message": "Kairon import started.", "data": batch}


@bp.route("/kairon/imports/<int:batch_id>")
class KaironImportProgress(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.response(200, KaironImportProgressEnvelopeSchema)
    def get(self, batch_id):
        batch = db.get_or_404(KaironUploadBatch, batch_id)
        return {"status": 200, "message": "Kairon import progress retrieved.", "data": batch}


@bp.route("/kairon/imports/<int:batch_id>/chunks/<int:chunk_number>")
class KaironImportChunks(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.arguments(KaironImportChunkSchema)
    @bp.response(200, KaironImportProgressEnvelopeSchema)
    def post(self, data, batch_id, chunk_number):
        batch, _chunk = process_import_chunk(
            batch_id=batch_id,
            chunk_number=chunk_number,
            checksum=data["checksum"],
            rows=data["rows"],
        )
        return {"status": 200, "message": f"Chunk {chunk_number} uploaded.", "data": batch}


@bp.route("/kairon/imports/<int:batch_id>/complete")
class KaironImportComplete(MethodView):
    @require_feature(REPORTS_FEATURE, access="write")
    @require_role("manager")
    @bp.response(200, KaironImportProgressEnvelopeSchema)
    def post(self, batch_id):
        batch = complete_cumulative_import(batch_id)
        return {"status": 200, "message": "Kairon import completed.", "data": batch}


@bp.route("/kairon/charts")
class KaironCharts(MethodView):
    @require_feature(REPORTS_FEATURE)
    @bp.arguments(KaironChartQuerySchema, location="query")
    @bp.response(200, KaironChartRecordListEnvelopeSchema)
    def get(self, args):
        # Only the current (non-superseded) batch per as-of date is ever
        # read here - see services.import_batch's note on why a re-upload
        # supersedes rather than merges.
        query = KaironChartRecord.query.join(KaironUploadBatch).filter(
            KaironUploadBatch.superseded_at.is_(None)
        )
        # Employee/lead chart reads obey report scope, including current coverage.
        # Managers retain the existing global import-inspection view here; their
        # team report endpoints continue enforcing their own reporting tree.
        role_type = g.user.role.role_type.code
        if role_type == "employee":
            query = query.filter(KaironChartRecord.user_id == g.user.id)
        elif role_type == "lead":
            query = query.filter(KaironChartRecord.user_id.in_(report_team_user_ids(g.user)))
        if role_type == "client":
            from app.clients import project_user_ids
            query = query.filter(KaironChartRecord.user_id.in_(project_user_ids(g.user)))
        if args.get("status"):
            query = query.filter(KaironChartRecord.status == args["status"])
        if args.get("level"):
            query = query.filter(KaironChartRecord.level == args["level"])
        if args.get("user_id"):
            query = query.filter(KaironChartRecord.user_id == args["user_id"])
        if args.get("user_ids"):
            query = query.filter(KaironChartRecord.user_id.in_(args["user_ids"]))
        if args.get("as_of_date"):
            query = query.filter(KaironUploadBatch.as_of_date == args["as_of_date"])
        records = query.order_by(KaironChartRecord.created_date.desc()).all()
        return {"status": 200, "message": "Kairon chart records retrieved successfully.", "data": records}
