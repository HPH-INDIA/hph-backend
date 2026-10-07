from app.extensions import db


class LoginHoursUploadBatch(db.Model):
    __tablename__ = "login_hours_upload_batches"

    id = db.Column(db.Integer, primary_key=True)
    source_filename = db.Column(db.String(255), nullable=False)
    source_format = db.Column(db.String(64), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    matched_count = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)

    upload_token = db.Column(db.String(36), nullable=True)
    chunk_hashes = db.Column(db.JSON, nullable=True)
    unmatched_names = db.Column(db.JSON, nullable=True)

    uploaded_by = db.relationship("User")


class LoginHourRecord(db.Model):
    __tablename__ = "login_hour_records"
    __table_args__ = (
        db.UniqueConstraint("user_id", "attendance_date", name="uq_login_hour_records_user_date"),
        db.Index("ix_login_hour_records_date", "attendance_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("login_hours_upload_batches.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    employee_name_raw = db.Column(db.String(255), nullable=False)
    personnel_id = db.Column(db.String(64), nullable=True)
    department = db.Column(db.String(128), nullable=True)
    first_in = db.Column(db.Time, nullable=True)
    last_out = db.Column(db.Time, nullable=True)
    total_inside_minutes = db.Column(db.Integer, nullable=False, default=0)
    total_outside_minutes = db.Column(db.Integer, nullable=False, default=0)
    total_span_minutes = db.Column(db.Integer, nullable=False, default=0)
    entries = db.Column(db.Integer, nullable=False, default=0)
    exits = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(64), nullable=True)
    anomalies = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now(), nullable=False
    )

    batch = db.relationship("LoginHoursUploadBatch", backref="records")
    user = db.relationship("User", backref="login_hour_records")
