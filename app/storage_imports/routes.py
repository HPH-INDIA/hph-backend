from flask import g
from flask.views import MethodView
from flask_smorest import abort
from app.auth import require_feature, require_role
from app.extensions import db
from app.storage_imports import bp
from app.storage_imports.embedded import kick_import_processing
from app.storage_imports.models import StorageImport
from app.storage_imports.schemas import PrepareSchema, CompleteSchema, PreparedEnvelope, ProgressEnvelope, ListEnvelope
from app.storage_imports.services import prepare, upload_complete, progress, retry, abandon


@bp.route("/file-imports")
class Imports(MethodView):
    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.arguments(PrepareSchema)
    @bp.response(201, PreparedEnvelope)
    def post(self, data):
        return {"status": 201, "message": "Upload authorized.", "data": prepare(data, g.user.id)}

    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.response(200, ListEnvelope)
    def get(self):
        jobs = StorageImport.query.filter_by(uploaded_by_id=g.user.id).order_by(StorageImport.created_at.desc()).limit(20).all()
        result = [progress(job) for job in jobs]
        if any(job.status in ("uploading", "queued", "processing") for job in jobs):
            kick_import_processing()
        return {"status": 200, "message": "File imports retrieved.", "data": result}


def owned(import_id):
    job = db.get_or_404(StorageImport, import_id)
    if job.uploaded_by_id != g.user.id:
        abort(403, message="This import belongs to another manager.")
    return job


@bp.route("/file-imports/<string:import_id>")
class ImportProgress(MethodView):
    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.response(200, ProgressEnvelope)
    def get(self, import_id):
        job = owned(import_id)
        result = progress(job)
        if job.status in ("uploading", "queued", "processing"):
            kick_import_processing()
        return {"status": 200, "message": "Import progress retrieved.", "data": result}


@bp.route("/file-imports/<string:import_id>/upload-complete")
class ImportComplete(MethodView):
    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.arguments(CompleteSchema)
    @bp.response(202, ProgressEnvelope)
    def post(self, data, import_id):
        result = upload_complete(owned(import_id), data)
        kick_import_processing()
        return {"status": 202, "message": "Import queued.", "data": result}


@bp.route("/file-imports/<string:import_id>/retry")
class ImportRetry(MethodView):
    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.response(202, ProgressEnvelope)
    def post(self, import_id):
        result = retry(owned(import_id))
        kick_import_processing()
        return {"status": 202, "message": "Import queued.", "data": result}


@bp.route("/file-imports/<string:import_id>/abandon")
class ImportAbandon(MethodView):
    @require_feature("reports", access="write")
    @require_role("manager")
    @bp.response(200, ProgressEnvelope)
    def post(self, import_id):
        return {"status": 200, "message": "Import abandoned.", "data": abandon(owned(import_id))}
