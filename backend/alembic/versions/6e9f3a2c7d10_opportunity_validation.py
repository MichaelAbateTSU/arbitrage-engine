"""Persist eligibility, candidate diagnostics and distinct shadow episodes."""

import sqlalchemy as sa

from alembic import op

revision = "6e9f3a2c7d10"
down_revision = "efb2041d5699"
branch_labels = None
depends_on = None


def upgrade():
    definitions = {
        "validation_eligibility": [
            sa.Column("venue", sa.String(32), nullable=False),
            sa.UniqueConstraint("source", "venue"),
        ],
        "candidate_validations": [
            sa.Column("match_id", sa.String(160), nullable=False),
            sa.Column("direction", sa.String(8), nullable=False),
        ],
        "validation_episodes": [
            sa.Column("match_id", sa.String(160), nullable=False),
            sa.Column("direction", sa.String(8), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
        ],
        "shadow_trials": [
            sa.Column("episode_id", sa.String(160), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        ],
        "validation_configuration": [
            sa.Column("revision", sa.Integer(), nullable=False),
        ],
    }
    indexed = {
        "validation_eligibility": ["venue"],
        "candidate_validations": ["match_id"],
        "validation_episodes": ["match_id"],
        "shadow_trials": ["episode_id", "state", "due_at"],
        "validation_configuration": [],
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
        for field in ["source", "created_at", *indexed[table]]:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    for table in (
        "validation_configuration",
        "shadow_trials",
        "validation_episodes",
        "candidate_validations",
        "validation_eligibility",
    ):
        op.drop_table(table)
