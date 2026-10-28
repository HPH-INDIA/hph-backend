from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from common.configdb import get_sql_alchemy
from config_dbname import DB_SCHEMA


db = get_sql_alchemy()

# JSONB on Postgres (Supabase), plain JSON elsewhere so tests can run on SQLite.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def utc_now():
    return datetime.now(timezone.utc)


class FormStatus:
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    ALL = (DRAFT, PUBLISHED, ARCHIVED)


class FormPurpose:
    APPRAISAL = "appraisal"
    ALL = (APPRAISAL,)


# Only Employees and Leads are appraised in v1, so only they can be assigned an appraisal form.
ASSIGNABLE_ROLE_TYPES = {FormPurpose.APPRAISAL: ("employee", "lead")}


class Form(db.Model):
    """A questionnaire being designed; published snapshots live in ``FormVersion``."""

    __tablename__ = "forms"
    __table_args__ = (
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_forms_status"),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    purpose = db.Column(db.String(32), nullable=False, default=FormPurpose.APPRAISAL)
    status = db.Column(db.String(16), nullable=False, default=FormStatus.DRAFT)
    # Work in progress; published versions are frozen copies of this at publish time.
    draft_definition = db.Column(JSONType, nullable=False)
    created_by = db.Column(db.Integer, nullable=False)
    updated_by = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    versions = db.relationship(
        "FormVersion",
        back_populates="form",
        order_by="FormVersion.version_no",
        cascade="all, delete-orphan",
    )
    assignments = db.relationship(
        "FormAssignment", back_populates="form", cascade="all, delete-orphan"
    )

    @property
    def latest_version(self):
        return self.versions[-1] if self.versions else None

    @property
    def active_assignments(self):
        return [assignment for assignment in self.assignments if assignment.active]


class FormVersion(db.Model):
    """An immutable published snapshot; appraisals pin one so later edits never change them."""

    __tablename__ = "form_versions"
    __table_args__ = (
        sa.UniqueConstraint("form_id", "version_no", name="uq_form_versions_form_version_no"),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(
        db.Integer, db.ForeignKey(f"{DB_SCHEMA}.forms.id", ondelete="CASCADE"), nullable=False
    )
    version_no = db.Column(db.Integer, nullable=False)
    definition = db.Column(JSONType, nullable=False)
    published_by = db.Column(db.Integer, nullable=False)
    published_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    form = db.relationship("Form", back_populates="versions")


class FormAssignment(db.Model):
    """Says which project and role type a form applies to; at most one active form per target."""

    __tablename__ = "form_assignments"
    __table_args__ = (
        sa.Index(
            "uq_form_assignments_active_target",
            "project_id",
            "role_type_code",
            "purpose",
            unique=True,
            postgresql_where=sa.text("active"),
            sqlite_where=sa.text("active"),
        ),
        {"schema": DB_SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(
        db.Integer, db.ForeignKey(f"{DB_SCHEMA}.forms.id", ondelete="CASCADE"), nullable=False
    )
    purpose = db.Column(db.String(32), nullable=False)
    project_id = db.Column(db.Integer, nullable=False)
    role_type_code = db.Column(db.String(32), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    deactivated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    form = db.relationship("Form", back_populates="assignments")


class FormTemplate(db.Model):
    """A ready-made definition admins copy into a new form."""

    __tablename__ = "form_templates"
    __table_args__ = ({"schema": DB_SCHEMA},)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    purpose = db.Column(db.String(32), nullable=False, default=FormPurpose.APPRAISAL)
    definition = db.Column(JSONType, nullable=False)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class FormEvent(db.Model):
    """Audit trail of form changes (NFR-3); kept when a never-published form is deleted."""

    __tablename__ = "form_events"
    __table_args__ = ({"schema": DB_SCHEMA},)

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(
        db.Integer, db.ForeignKey(f"{DB_SCHEMA}.forms.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(32), nullable=False)
    details = db.Column(JSONType, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
