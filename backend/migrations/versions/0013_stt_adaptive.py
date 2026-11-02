"""Per-chunk transcription checkpoints, a shared concurrency budget, and the last stable level."""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"


def upgrade():
    op.create_table(
        "stt_chunks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "meeting_id", sa.String(32), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("offset_seconds", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("segments", sa.JSON, nullable=False),
        sa.Column("language", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(200)),
        sa.Column("cost", sa.Numeric(18, 9)),
        sa.Column("cost_status", sa.String(20)),
        sa.Column("created", sa.Float, nullable=False),
        sa.UniqueConstraint("job_id", "chunk_index"),
    )
    op.create_index("ix_stt_chunks_meeting_id", "stt_chunks", ["meeting_id"])
    op.create_table(
        "stt_leases",
        sa.Column("slot", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
    )
    op.create_table(
        "stt_state",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("updated", sa.Float, nullable=False),
        sa.Column("control", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_table("stt_state")
    op.drop_table("stt_leases")
    op.drop_index("ix_stt_chunks_meeting_id", table_name="stt_chunks")
    op.drop_table("stt_chunks")
