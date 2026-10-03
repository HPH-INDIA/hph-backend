from app.extensions import db


class StorageImport(db.Model):
    __tablename__ = "storage_imports"
    id = db.Column(db.String(36), primary_key=True)
    kind = db.Column(db.String(16), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    source_filename = db.Column(db.String(255), nullable=False)
    source_checksum = db.Column(db.String(64), nullable=False)
    object_path = db.Column(db.String(255), nullable=False, unique=True)
    wrapped_key = db.Column(db.LargeBinary, nullable=False)
    status = db.Column(db.String(24), nullable=False, default="uploading", index=True)
    total_rows = db.Column(db.Integer, nullable=False)
    file_size = db.Column(db.Integer)
    file_checksum = db.Column(db.String(64))
    batch_id = db.Column(db.Integer)
    error = db.Column(db.String(500))
    attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), onupdate=db.func.now())
    uploaded_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))


class StorageImportSlot(db.Model):
    __tablename__ = "storage_import_slots"
    kind = db.Column(db.String(16), primary_key=True)
    import_id = db.Column(db.String(36), db.ForeignKey("storage_imports.id"))
