"""named strategy versions

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import algotrade.db.models

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("risk", sa.JSON(), nullable=False),
        sa.Column("strategy", sa.JSON(), nullable=False),
        sa.Column("created_at", algotrade.db.models.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", algotrade.db.models.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("strategy_profiles")
