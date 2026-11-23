import sqlalchemy as sa

from config_dbname import DB_SCHEMA
from models.base import JSONType, db, utc_now


class AppraisalStatus:
    NOT_STARTED = "not_started"
    DRAFT = "draft"
    AWAITING_REVIEW = "awaiting_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    # The reporting line could not supply every reviewer; an admin must assign one.
    NEEDS_REVIEWER = "needs_reviewer"
    ALL = (NOT_STARTED, DRAFT, AWAITING_REVIEW, CHANGES_REQUESTED, APPROVED, NEEDS_REVIEWER)
    EDITABLE_BY_EMPLOYEE = (NOT_STARTED, DRAFT, CHANGES_REQUESTED)


class ReviewStage:
    EMPLOYEE = "employee"
    LEAD = "lead"
    MANAGER = "manager"
    REVIEWERS = (LEAD, MANAGER)
    ALL = (EMPLOYEE, LEAD, MANAGER)


class StepStatus:
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    ALL = (PENDING, ACTIVE, COMPLETED, RETURNED, CANCELLED)


class CommentKind:
    FEEDBACK = "feedback"
    ONE_TO_ONE_NOTE = "one_to_one_note"
    COMMENT = "comment"
    ALL = (FEEDBACK, ONE_TO_ONE_NOTE, COMMENT)


class Appraisal(db.Model):
    """One appraised person (Employee or Lead) in one cycle, with a snapshot of who reviews them.

    The form definition, project, role type and reviewers are copied at launch so later org or
    form changes never rewrite an appraisal that is under way.
    """

    __tablename__ = "appraisals"
    __table_args__ = (
        sa.UniqueConstraint("cycle_id", "user_id", name="uq_appraisals_cycle_user"),
        sa.CheckConstraint(
            "status IN ('not_started', 'draft', 'awaiting_review', 'changes_requested', "
            "'approved', 'needs_reviewer')",
            name="ck_appraisals_status",
        ),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(
        db.Integer, db.ForeignKey(f"{DB_SCHEMA}.cycles.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(db.Integer, nullable=False, index=True)
    employee_name = db.Column(db.String(300), nullable=False)
    employee_emp_id = db.Column(db.String(64), nullable=True)
    project_id = db.Column(db.Integer, nullable=True)
    role_type_code = db.Column(db.String(32), nullable=False)
    form_id = db.Column(db.Integer, nullable=False)
    form_version_id = db.Column(db.Integer, nullable=False)
    form_name = db.Column(db.String(200), nullable=False)
    form_definition = db.Column(JSONType, nullable=False)
    lead_user_id = db.Column(db.Integer, nullable=True, index=True)
    lead_name = db.Column(db.String(300), nullable=True)
    manager_user_id = db.Column(db.Integer, nullable=True, index=True)
    manager_name = db.Column(db.String(300), nullable=True)
    current_reviewer_user_id = db.Column(db.Integer, nullable=True, index=True)
    current_review_step_id = db.Column(db.Integer, nullable=True)
    review_attempt = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(32), nullable=False, default=AppraisalStatus.NOT_STARTED)
    needs_reviewer_reason = db.Column(db.String(300), nullable=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    one_to_one_held_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    cycle = db.relationship("Cycle", back_populates="appraisals")
    steps = db.relationship(
        "ReviewStep",
        back_populates="appraisal",
        order_by=lambda: [ReviewStep.review_attempt, ReviewStep.sequence_no],
        cascade="all, delete-orphan",
    )
    answers = db.relationship("Answer", back_populates="appraisal", cascade="all, delete-orphan")
    comments = db.relationship(
        "ReviewComment",
        back_populates="appraisal",
        order_by="ReviewComment.id",
        cascade="all, delete-orphan",
    )

    def steps_for_attempt(self, attempt=None):
        attempt = self.review_attempt if attempt is None else attempt
        return [step for step in self.steps if step.review_attempt == attempt]

    @property
    def current_step(self):
        if self.current_review_step_id is None:
            return None
        return next((step for step in self.steps if step.id == self.current_review_step_id), None)


class ReviewStep(db.Model):
    """One reviewer's turn in one attempt: Lead then Manager for an Employee, Manager for a Lead."""

    __tablename__ = "appraisal_review_steps"
    __table_args__ = (
        sa.UniqueConstraint(
            "appraisal_id", "review_attempt", "sequence_no", name="uq_review_steps_order"
        ),
        sa.Index("ix_review_steps_reviewer_status", "reviewer_user_id", "status"),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    appraisal_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{DB_SCHEMA}.appraisals.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_attempt = db.Column(db.Integer, nullable=False)
    sequence_no = db.Column(db.Integer, nullable=False)
    review_stage = db.Column(db.String(16), nullable=False)
    reviewer_user_id = db.Column(db.Integer, nullable=False)
    reviewer_name = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(16), nullable=False, default=StepStatus.PENDING)
    activated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    appraisal = db.relationship("Appraisal", back_populates="steps")


class Answer(db.Model):
    """One field's value for one stage in one attempt.

    Keyed by (appraisal, attempt, stage, field) so a reviewer can never overwrite the employee's
    answers or another reviewer's, and a resubmission keeps the earlier attempt as history.
    """

    __tablename__ = "appraisal_answers"
    __table_args__ = ({"schema": DB_SCHEMA},)

    appraisal_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{DB_SCHEMA}.appraisals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    review_attempt = db.Column(db.Integer, primary_key=True)
    review_stage = db.Column(db.String(16), primary_key=True)
    field_key = db.Column(db.String(64), primary_key=True)
    value = db.Column(JSONType, nullable=True)
    updated_by = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    appraisal = db.relationship("Appraisal", back_populates="answers")


class ReviewComment(db.Model):
    """Feedback sent back to the employee, one-to-one notes, and optional reviewer comments."""

    __tablename__ = "review_comments"
    __table_args__ = ({"schema": DB_SCHEMA},)

    id = db.Column(db.Integer, primary_key=True)
    appraisal_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{DB_SCHEMA}.appraisals.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_step_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{DB_SCHEMA}.appraisal_review_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_attempt = db.Column(db.Integer, nullable=False)
    review_stage = db.Column(db.String(16), nullable=False)
    author_user_id = db.Column(db.Integer, nullable=False)
    author_name = db.Column(db.String(300), nullable=True)
    kind = db.Column(db.String(32), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    appraisal = db.relationship("Appraisal", back_populates="comments")


class AppraisalEvent(db.Model):
    """Audit trail of every status change and review decision (NFR-3)."""

    __tablename__ = "appraisal_events"
    __table_args__ = ({"schema": DB_SCHEMA},)

    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(
        db.Integer, db.ForeignKey(f"{DB_SCHEMA}.cycles.id", ondelete="CASCADE"), nullable=False
    )
    appraisal_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{DB_SCHEMA}.appraisals.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    actor_user_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(48), nullable=False)
    from_status = db.Column(db.String(32), nullable=True)
    to_status = db.Column(db.String(32), nullable=True)
    details = db.Column(JSONType, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
