from app.extensions import db


class LeadCoverage(db.Model):
    """An explicit, dated grant. Approval and cancellation audit are retained."""

    __tablename__ = "lead_coverages"
    __table_args__ = (
        db.CheckConstraint("source_lead_id <> covering_lead_id", name="ck_lead_coverage_distinct_leads"),
        db.CheckConstraint("ends_on >= starts_on", name="ck_lead_coverage_date_range"),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_lead_coverage_status",
        ),
        db.Index("ix_lead_coverage_source_dates", "source_lead_id", "starts_on", "ends_on"),
        db.Index("ix_lead_coverage_covering_status", "covering_lead_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_lead_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    covering_lead_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(2000), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="pending")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    source_lead = db.relationship("User", foreign_keys=[source_lead_id])
    covering_lead = db.relationship("User", foreign_keys=[covering_lead_id])
