import sqlalchemy as sa

from config_dbname import DB_SCHEMA
from models.base import JSONType, db, utc_now


class CycleStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"
    ALL = (DRAFT, ACTIVE, CLOSED)


class CycleScope:
    ORGANIZATION = "organization"
    PROJECT = "project"
    ALL = (ORGANIZATION, PROJECT)


class CycleFrequency:
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    ANNUAL = "annual"
    CUSTOM = "custom"
    ALL = (QUARTERLY, HALF_YEARLY, ANNUAL, CUSTOM)


class Cycle(db.Model):
    """An appraisal period (FR-1, FR-2); launching it creates the appraisals."""

    __tablename__ = "cycles"
    __table_args__ = (
        sa.CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_cycles_status"),
        sa.CheckConstraint("scope IN ('organization', 'project')", name="ck_cycles_scope"),
        sa.CheckConstraint(
            "frequency IN ('quarterly', 'half_yearly', 'annual', 'custom')",
            name="ck_cycles_frequency",
        ),
        sa.CheckConstraint(
            "(scope = 'project') = (project_id IS NOT NULL)", name="ck_cycles_project_scope"
        ),
        sa.CheckConstraint("end_date >= start_date", name="ck_cycles_dates"),
        sa.CheckConstraint("review_due >= submission_due", name="ck_cycles_due_dates"),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    scope = db.Column(db.String(16), nullable=False)
    project_id = db.Column(db.Integer, nullable=True)
    frequency = db.Column(db.String(16), nullable=False, default=CycleFrequency.ANNUAL)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    submission_due = db.Column(db.Date, nullable=False)
    review_due = db.Column(db.Date, nullable=False)
    require_one_to_one = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(16), nullable=False, default=CycleStatus.DRAFT)
    # Who was skipped at launch and why, so admins can fix data and relaunch.
    launch_summary = db.Column(JSONType, nullable=True)
    created_by = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    launched_by = db.Column(db.Integer, nullable=True)
    launched_at = db.Column(db.DateTime(timezone=True), nullable=True)
    closed_by = db.Column(db.Integer, nullable=True)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    appraisals = db.relationship("Appraisal", back_populates="cycle")
