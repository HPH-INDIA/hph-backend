"""Add the practices master list and CODING IT tickets.

New tables: practices, practice_status_history, practice_assignments,
practice_requests, issue_categories, it_tickets, it_ticket_events and
manual_record_tickets. Adds kairon_chart_records.practice_id (backfilled from
the free-text practice name once practices exist). Seeds the `it_tickets` and
`practices` features (both appear in Roles & Features) and the issue
categories from the IT tickets tracker.

Every new table follows the backend-only access model: RLS on, nothing
granted to PUBLIC / anon / authenticated, full access for hph_app when that
role exists (same as lead_coverages and the Entra tables).
"""
from alembic import op
import sqlalchemy as sa

revision = "b3d9e1f2a7c4"
down_revision = "a8e2c4f60139"
branch_labels = None
depends_on = None

_PRACTICE_STATUSES = "'Active', 'Inactive', 'Terminated'"
_ACCESS_STATUSES = (
    "'Request to Practice Sent', 'Credentials Received from Practice', 'Credentials Shared with User', "
    "'Access Live', 'IT Ticket Received', 'Not Found', 'Request Cancelled', 'Access Closed'"
)
_TABLES = (
    "practices", "practice_status_history", "practice_assignments", "practice_requests",
    "issue_categories", "it_tickets", "it_ticket_events", "manual_record_tickets",
)
_SEQUENCES = (
    "practices_id_seq", "practice_status_history_id_seq", "practice_assignments_id_seq",
    "practice_requests_id_seq", "issue_categories_id_seq", "it_tickets_id_seq", "it_ticket_events_id_seq",
)
_ISSUE_CATEGORIES = (
    "Received credentials but unable to login",
    "Did not receive login credentials",
    "Did not receive login URL",
    "Invalid credentials",
    "Practice access code issue",
    "Unable to search patient name",
    "VPN/RDP issue",
    "Admin credentials needed to install remote apps",
    "Time zone",
    "Latency issue",
    "Limited access to patient documents",
    "Verification code",
    "Microsoft Authenticator / DUO issue",
    "Access denied to patient portal",
    "Unable to login after password reset",
    "Missing chart hyperlink",
    "Patients not found in EMR",
    "Other",
)
# (codename, title, description, readable role types, writable role types)
_FEATURES = (
    ("it_tickets", "IT Tickets",
     "Log and follow IT (Jira) tickets for practices that block a Coder (1LR) or QA (2LR).",
     ("employee", "lead", "manager", "admin", "super_admin"),
     ("employee", "lead", "manager", "admin", "super_admin")),
    ("practices", "Practices",
     "Practice master list, assignments, access status and practice status.",
     ("employee", "lead", "manager", "admin", "super_admin"),
     ("manager", "admin", "super_admin")),
)


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    op.create_table(
        "practices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("tin", sa.String(32), nullable=True),
        sa.Column("worktype", sa.String(64), nullable=True),
        sa.Column("priority_rank", sa.Integer(), nullable=True),
        sa.Column("cms_count", sa.Integer(), nullable=True),
        sa.Column("emr", sa.String(255), nullable=True),
        sa.Column("affiliation", sa.String(255), nullable=True),
        sa.Column("pipeline_2027", sa.String(128), nullable=True),
        sa.Column("filter_72", sa.String(16), nullable=True),
        sa.Column("onshore_coders", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="Active"),
        sa.Column("status_effective_from", sa.Date(), nullable=True),
        sa.Column("status_reason", sa.String(500), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tin", name="uq_practices_tin"),
        sa.CheckConstraint(f"status IN ({_PRACTICE_STATUSES})", name="ck_practices_status"),
        sa.CheckConstraint("cms_count IS NULL OR cms_count >= 0", name="ck_practices_cms_count"),
    )
    op.create_index("ix_practices_name_key", "practices", ["name_key"])
    op.create_index("ix_practices_project_status", "practices", ["project_id", "status"])

    op.create_table(
        "practice_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("auto_resolved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_practice_status_history_practice_id", "practice_status_history", ["practice_id"])

    op.create_table(
        "practice_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("access_status", sa.String(64), nullable=True),
        sa.Column("access_status_since", sa.Date(), nullable=True),
        sa.Column("assigned_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("practice_id", "user_id", name="uq_practice_assignments_practice_user"),
        sa.CheckConstraint(f"access_status IS NULL OR access_status IN ({_ACCESS_STATUSES})",
                           name="ck_practice_assignments_access_status"),
    )
    op.create_index("ix_practice_assignments_user", "practice_assignments", ["user_id"])

    op.create_table(
        "practice_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tin", sa.String(32), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'assigned', 'created', 'rejected')", name="ck_practice_requests_status"),
    )
    op.create_index("ix_practice_requests_status", "practice_requests", ["status"])

    op.create_table(
        "issue_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_issue_categories_name"),
    )

    op.create_table(
        "it_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jira_key", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("practice_id", sa.Integer(), sa.ForeignKey("practices.id"), nullable=False),
        sa.Column("issue_category_id", sa.Integer(), sa.ForeignKey("issue_categories.id"), nullable=True),
        sa.Column("hph_status_note", sa.Text(), nullable=True),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("resolved_on", sa.Date(), nullable=True),
        sa.Column("resolution_note", sa.String(500), nullable=True),
        sa.Column("charts_available", sa.Integer(), nullable=True),
        sa.Column("not_in_kairon", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("charts_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("follow_up_issue", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("jira_key", name="uq_it_tickets_jira_key"),
        sa.CheckConstraint("status IN ('in_progress', 'resolved')", name="ck_it_tickets_status"),
        sa.CheckConstraint("source IN ('manual', 'import')", name="ck_it_tickets_source"),
        sa.CheckConstraint("jira_key ~ '^VTEC-[0-9]+$'", name="ck_it_tickets_jira_key"),
        sa.CheckConstraint("charts_available IS NULL OR charts_available >= 0", name="ck_it_tickets_charts"),
        sa.CheckConstraint("resolved_on IS NULL OR resolved_on >= opened_on", name="ck_it_tickets_resolved_after_opened"),
    )
    op.create_index("ix_it_tickets_user_status", "it_tickets", ["user_id", "status"])
    op.create_index("ix_it_tickets_practice_status", "it_tickets", ["practice_id", "status"])
    op.create_index("ix_it_tickets_opened_on", "it_tickets", ["opened_on"])

    op.create_table(
        "it_ticket_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("jira_key", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_it_ticket_events_ticket", "it_ticket_events", ["ticket_id", "created_at"])

    op.create_table(
        "manual_record_tickets",
        sa.Column("manual_record_id", sa.Integer(),
                  sa.ForeignKey("manual_daily_records.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("it_tickets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hours", sa.Numeric(4, 2), nullable=False),
        sa.CheckConstraint("hours >= 0 AND hours <= 10", name="ck_manual_record_tickets_hours"),
    )
    op.create_index("ix_manual_record_tickets_ticket_id", "manual_record_tickets", ["ticket_id"])

    op.add_column("kairon_chart_records", sa.Column(
        "practice_id", sa.Integer(), sa.ForeignKey("practices.id", ondelete="SET NULL",
                                                    name="fk_kairon_chart_records_practice_id"), nullable=True))
    op.create_index("ix_kairon_chart_records_practice_id", "kairon_chart_records", ["practice_id"])

    connection = op.get_bind()
    connection.execute(
        sa.text("INSERT INTO issue_categories (name) VALUES " + ", ".join(f"(:c{i})" for i in range(len(_ISSUE_CATEGORIES)))),
        {f"c{i}": name for i, name in enumerate(_ISSUE_CATEGORIES)},
    )
    for codename, title, description, readable, writable in _FEATURES:
        feature_id = connection.execute(sa.text(
            "INSERT INTO features (codename, title, description, active) VALUES (:code, :title, :description, true) "
            "ON CONFLICT (codename) DO UPDATE SET title = EXCLUDED.title RETURNING id"),
            dict(code=codename, title=title, description=description)).scalar_one()
        connection.execute(sa.text(
            "INSERT INTO role_features (role_id, feature_id, can_read, can_write) "
            "SELECT r.id, :feature_id, true, rt.code = ANY(:writable) FROM roles r "
            "JOIN role_types rt ON rt.id = r.role_type_id WHERE rt.code = ANY(:readable) "
            "ON CONFLICT (role_id, feature_id) DO NOTHING"),
            dict(feature_id=feature_id, readable=list(readable), writable=list(writable)))

    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public.{table} FROM anon; END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public.{table} FROM authenticated; END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hph_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO hph_app;
                CREATE POLICY {table}_backend ON public.{table} TO hph_app USING (true) WITH CHECK (true);
            END IF;
        END $$""")
    for sequence in _SEQUENCES:
        op.execute(f"REVOKE ALL ON SEQUENCE public.{sequence} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON SEQUENCE public.{sequence} FROM anon; END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON SEQUENCE public.{sequence} FROM authenticated; END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hph_app') THEN GRANT USAGE, SELECT ON SEQUENCE public.{sequence} TO hph_app; END IF;
        END $$""")


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text(
        "DELETE FROM role_features WHERE feature_id IN (SELECT id FROM features WHERE codename IN ('it_tickets', 'practices'))"))
    connection.execute(sa.text("DELETE FROM features WHERE codename IN ('it_tickets', 'practices')"))
    op.drop_index("ix_kairon_chart_records_practice_id", table_name="kairon_chart_records")
    op.drop_constraint("fk_kairon_chart_records_practice_id", "kairon_chart_records", type_="foreignkey")
    op.drop_column("kairon_chart_records", "practice_id")
    for table in reversed(_TABLES):
        op.drop_table(table)
