"""Persist policy screening, per-instrument monitoring and conservative coverage."""

import sqlalchemy as sa

from alembic import op

revision = "93ad7c201b46"
down_revision = "6e9f3a2c7d10"
branch_labels = None
depends_on = None


def upgrade():
    definitions = {
        "validation_rule_families": [],
        "validation_book_monitoring": [
            sa.Column("market_id", sa.String(160), nullable=False),
        ],
        "validation_observation_coverage": [
            sa.Column("match_id", sa.String(160), nullable=False),
            sa.Column("day", sa.String(10), nullable=False),
            sa.UniqueConstraint("source", "match_id", "day"),
        ],
    }
    for table, columns in definitions.items():
        op.create_table(
            table,
            sa.Column("id", sa.String(160), primary_key=True),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            *columns,
        )
        indexed = ["source", "created_at"]
        if table != "validation_rule_families":
            indexed.append("market_id" if table == "validation_book_monitoring" else "match_id")
        if table == "validation_observation_coverage":
            indexed.append("day")
        for field in indexed:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    for table in (
        "validation_observation_coverage",
        "validation_book_monitoring",
        "validation_rule_families",
    ):
        op.drop_table(table)
