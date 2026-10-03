"""append-only guards on audit_log, order_events and fills (PostgreSQL)

Rows in these tables are evidence; the application may insert but never
update or delete them (docs/design.md §3, docs/security.md §4).

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("audit_log", "order_events", "fills")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for t in TABLES:
        op.execute(f"""
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
            FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation()")
