from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm.attributes import get_history

from app.extensions import db

_PROTECTED_STAGE_FIELDS = ("code", "sort_order", "duration_days")

VALID_PERIOD_SOURCES = ("observed_first_activity", "calendar_offset", "manual_override")
VALID_REVIEW_STATUSES = ("pending", "resolved")


def _sql_in_list(values):
    return ", ".join(f"'{v}'" for v in values)


class Stage(db.Model):
    """Seed data (Training, M1-M4, Steady State) - fixed path order and
    durations, per the dev spec's "durations are fixed per the earlier
    decision" note. Not editable via the config page.
    """

    __tablename__ = "stages"

    code = db.Column(db.String(32), primary_key=True)
    sort_order = db.Column(db.Integer, unique=True, nullable=False)
    duration_days = db.Column(db.Integer, nullable=True)  # null = open-ended (Steady State)

    def __repr__(self):
        return f"<Stage {self.code}>"


@event.listens_for(Stage, "before_update")
def _protect_stage_identity(mapper, connection, target):
    for field in _PROTECTED_STAGE_FIELDS:
        if get_history(target, field).has_changes():
            raise ValueError(f"Stage.{field} is fixed seed data and cannot be changed.")


@event.listens_for(Stage, "before_delete")
def _protect_stage_deletion(mapper, connection, target):
    raise ValueError("Stage rows are fixed seed data and cannot be deleted.")


class Cohort(db.Model):
    __tablename__ = "cohorts"

    id = db.Column(db.Integer, primary_key=True)
    sequence_no = db.Column(db.Integer, unique=True, nullable=False)
    label = db.Column(db.String(128), nullable=False)
    window_start = db.Column(db.Date, nullable=False)
    window_end = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    def __repr__(self):
        return f"<Cohort {self.label}>"


class CohortMembership(db.Model):
    """A CODING lead or employee assigned to one operational cohort.

    Users are the production identity in the application.  This table is
    intentionally separate from the legacy ``coders`` import model so new
    cohorts never depend on display-name matching.
    """

    __tablename__ = "cohort_memberships"
    __table_args__ = (
        db.UniqueConstraint("user_id", name="uq_cohort_memberships_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    joined_on = db.Column(db.Date, nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    cohort = db.relationship("Cohort", backref="user_memberships")
    user = db.relationship("User", foreign_keys=[user_id], backref="cohort_membership")
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])


class UserStagePeriod(db.Model):
    """Effective stage periods for a CODING user, independent of role title."""

    __tablename__ = "user_stage_periods"
    __table_args__ = (
        db.CheckConstraint(
            f"source IN ({_sql_in_list(VALID_PERIOD_SOURCES)})", name="ck_user_stage_periods_source"
        ),
        db.UniqueConstraint("user_id", "stage_code", name="uq_user_stage_periods_user_stage"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    stage_code = db.Column(db.String(32), db.ForeignKey("stages.code"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    source = db.Column(db.String(32), nullable=False)
    shifted_by_exception_days = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship("User", backref="stage_periods")
    stage = db.relationship("Stage")


class Coder(db.Model):
    __tablename__ = "coders"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(255), unique=True, nullable=False)
    join_date = db.Column(db.Date, nullable=False)
    cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id"), nullable=True)

    cohort = db.relationship("Cohort", backref="members")

    def __repr__(self):
        return f"<Coder {self.full_name}>"


class CoderAlias(db.Model):
    """Resolves the inconsistent short names the manual/Kairon/ramp files use
    for the same coder (e.g. "Charishma" vs "Charishma Sonani") to a single
    `coders` row - seeded from the legacy pipeline's own NAME_MAP during the
    one-time import, and extendable as new aliases turn up.
    """

    __tablename__ = "coder_aliases"

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    alias = db.Column(db.String(255), unique=True, nullable=False)

    coder = db.relationship("Coder", backref="aliases")

    def __repr__(self):
        return f"<CoderAlias {self.alias!r}>"


class CoderStagePeriod(db.Model):
    """One row per coder per stage - a computed cache, not hand-edited data.
    Rebuilt in full by compute_stage_periods() whenever new activity lands,
    an exception is recorded, or the coder's cohort changes; never written
    to directly by the API.
    """

    __tablename__ = "coder_stage_periods"
    __table_args__ = (
        db.CheckConstraint(
            f"source IN ({_sql_in_list(VALID_PERIOD_SOURCES)})", name="ck_coder_stage_periods_source"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    stage_code = db.Column(db.String(32), db.ForeignKey("stages.code"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    source = db.Column(db.String(32), nullable=False)
    shifted_by_exception_days = db.Column(db.Integer, nullable=False, default=0)

    coder = db.relationship("Coder", backref="stage_periods")
    stage = db.relationship("Stage")

    def __repr__(self):
        return f"<CoderStagePeriod coder_id={self.coder_id} stage={self.stage_code}>"


class StageTargetRule(db.Model):
    __tablename__ = "stage_target_rules"
    __table_args__ = (
        db.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_stage_target_rules_valid_range",
        ),
        # A plain btree unique/check can't express "no overlapping date
        # ranges for the same stage" - only a GiST exclusion constraint can.
        # Mirrored here (rather than left as migration-only DDL) so
        # db.create_all() - what the test suite uses - gets the same
        # protection the real migration creates; kept under the identical
        # name so a future `flask db migrate` sees no spurious diff. Needs
        # the btree_gist extension (see the migration) for the stage_code
        # equality term - GiST alone has no native text/varchar equality.
        ExcludeConstraint(
            (db.column("stage_code"), "="),
            (text("daterange(effective_from, effective_to)"), "&&"),
            using="gist",
            name="ex_stage_target_rules_no_overlap",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    stage_code = db.Column(db.String(32), db.ForeignKey("stages.code"), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)  # null = open-ended (current rule)
    daily_target = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    stage = db.relationship("Stage")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<StageTargetRule {self.stage_code} from={self.effective_from}>"


class CohortJoinReview(db.Model):
    """The manual-assignment queue for a join date that falls near more than
    one cohort's window - deliberately has no auto-resolve path.
    """

    __tablename__ = "cohort_join_reviews"
    __table_args__ = (
        db.CheckConstraint(
            f"status IN ({_sql_in_list(VALID_REVIEW_STATUSES)})", name="ck_cohort_join_reviews_status"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    candidate_cohort_ids = db.Column(db.ARRAY(db.Integer), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending")
    resolved_cohort_id = db.Column(db.Integer, db.ForeignKey("cohorts.id"), nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    coder = db.relationship("Coder")
    resolved_cohort = db.relationship("Cohort")
    resolved_by = db.relationship("User")

    def __repr__(self):
        return f"<CohortJoinReview coder_id={self.coder_id} status={self.status}>"


class StageException(db.Model):
    """An individual coder's extra time in one stage - shifts only that
    coder's own downstream schedule, never the cohort baseline.
    """

    __tablename__ = "stage_exceptions"

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    stage_code = db.Column(db.String(32), db.ForeignKey("stages.code"), nullable=False)
    extra_days = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    coder = db.relationship("Coder", backref="stage_exceptions")
    stage = db.relationship("Stage")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<StageException coder_id={self.coder_id} stage={self.stage_code} +{self.extra_days}d>"


class ManualProductionFact(db.Model):
    """One row per coder per day of manual chart-count data - the source
    first_activity_date() reads first. Ingested from the daily manual
    production file the legacy pipeline already reads (see readers.py's
    'Production count today' column); a re-drop for an already-loaded day
    upserts on (coder_id, activity_date), matching the legacy "newest file
    wins" rule.
    """

    __tablename__ = "manual_production_facts"
    __table_args__ = (db.UniqueConstraint("coder_id", "activity_date", name="uq_manual_production_fact"),)

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    activity_date = db.Column(db.Date, nullable=False)
    production_count = db.Column(db.Integer, nullable=False, default=0)
    ingested_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    coder = db.relationship("Coder")

    def __repr__(self):
        return f"<ManualProductionFact coder_id={self.coder_id} date={self.activity_date}>"


class KaironCompletion(db.Model):
    """One row per completed chart, deduplicated on the chart itself - the
    same (MBI, Level, Coder, Completed date) key the legacy pipeline already
    uses, so re-ingesting an overlapping dump is a no-op rather than double
    counting. Only Completed-status rows; holds/active charts belong to the
    (out of scope) 1LR/2LR/3LR review tooling.
    """

    __tablename__ = "kairon_completions"
    __table_args__ = (
        db.UniqueConstraint("mbi", "level", "coder_id", "completed_date", name="uq_kairon_completion"),
    )

    id = db.Column(db.Integer, primary_key=True)
    coder_id = db.Column(db.Integer, db.ForeignKey("coders.id"), nullable=False)
    mbi = db.Column(db.String(64), nullable=False)
    level = db.Column(db.String(32), nullable=False)
    completed_date = db.Column(db.Date, nullable=False)
    ingested_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    coder = db.relationship("Coder")

    def __repr__(self):
        return f"<KaironCompletion coder_id={self.coder_id} mbi={self.mbi} date={self.completed_date}>"


class UserStageEvidence(db.Model):
    """Anchors from the last successfully completed Kairon refresh."""
    __tablename__ = "user_stage_evidence"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    first_completed = db.Column(db.Date, nullable=True)
    first_pvp_completed = db.Column(db.Date, nullable=True)
    first_foundation_completed = db.Column(db.Date, nullable=True)
    refreshed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class FoundationTargetRule(db.Model):
    __tablename__ = "foundation_target_rules"
    __table_args__ = (
        db.CheckConstraint("stage_code IN ('W1','W2','W3','W4','Steady State')", name="ck_foundation_target_stage"),
        db.CheckConstraint("daily_target >= 0", name="ck_foundation_target_nonnegative"),
        db.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_foundation_target_range"),
        ExcludeConstraint(
            (db.column("stage_code"), "="),
            (text("daterange(effective_from, effective_to)"), "&&"),
            using="gist", name="ex_foundation_target_no_overlap",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    stage_code = db.Column(db.String(32), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)
    daily_target = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class StageTargetChange(db.Model):
    """Immutable audit of a target replacement and its recalculation scope."""
    __tablename__ = "stage_target_changes"
    __table_args__ = (
        db.CheckConstraint("program IN ('main','foundation')", name="ck_target_change_program"),
        db.CheckConstraint("apply_from IN ('today','program_start','scheduled')", name="ck_target_change_scope"),
        db.CheckConstraint("daily_target >= 0 AND recalculated_records >= 0", name="ck_target_change_nonnegative"),
    )
    id = db.Column(db.Integer, primary_key=True)
    program = db.Column(db.String(16), nullable=False)
    stage_code = db.Column(db.String(32), nullable=False)
    apply_from = db.Column(db.String(16), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    daily_target = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    reason = db.Column(db.Text, nullable=True)
    previous_rules = db.Column(db.JSON, nullable=False)
    recalculated_records = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
