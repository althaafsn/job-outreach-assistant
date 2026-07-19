"""Track whether discovered jobs have verified posting data."""

import sqlalchemy as sa

from alembic import op

revision = "20260719_0002"
down_revision = "20260718_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("quality_status", sa.String(length=32), server_default="pending", nullable=False),
    )
    op.add_column("jobs", sa.Column("extraction_error", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("extraction_model", sa.String(length=120), nullable=True))
    op.add_column(
        "jobs", sa.Column("extraction_prompt_version", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "jobs",
        sa.Column("extraction_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("jobs", sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("source_content_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_jobs_quality_status", "jobs", ["quality_status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_quality_status", table_name="jobs")
    for column in (
        "source_content_hash",
        "extracted_at",
        "extraction_attempts",
        "extraction_prompt_version",
        "extraction_model",
        "extraction_error",
        "quality_status",
    ):
        op.drop_column("jobs", column)
