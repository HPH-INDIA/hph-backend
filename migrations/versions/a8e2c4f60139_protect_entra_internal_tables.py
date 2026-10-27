"""Keep internal Entra tables inaccessible through the Supabase Data API."""
from alembic import op

revision = "a8e2c4f60139"
down_revision = "f6c3d7a90128"
branch_labels = None
depends_on = None

_TABLES = ("external_identities", "identity_audits", "auth_transactions")
_SEQUENCES = ("external_identities_id_seq", "identity_audits_id_seq")


def upgrade():
    # Flask connects using the privileged backend database role. These tables are
    # never queried directly by browser Supabase clients; no public policies apply.
    for table in _TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public.{table} FROM PUBLIC')
        for role in ("anon", "authenticated"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON TABLE public.{table} FROM {role};
                END IF;
            END $$""")
    for sequence in _SEQUENCES:
        op.execute(f'REVOKE ALL ON SEQUENCE public.{sequence} FROM PUBLIC')
        for role in ("anon", "authenticated"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON SEQUENCE public.{sequence} FROM {role};
                END IF;
            END $$""")


def downgrade():
    # Retain protective access controls when rolling back app versions. The
    # original feature downgrade drops these tables when they are retired.
    pass
