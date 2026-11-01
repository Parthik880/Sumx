"""Persist narration lifecycle and real streaming measurements without changing old audio."""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"


def upgrade():
    with op.batch_alter_table("jobs") as b:
        for name, size in [("user_id", 32), ("voice_id", 32), ("model_name", 200), ("narration_stage", 32)]:
            b.add_column(sa.Column(name, sa.String(size)))
        for name in ["started_at", "completed_at"]:
            b.add_column(sa.Column(name, sa.Float()))
        b.add_column(sa.Column("output_path", sa.Text()))
        b.add_column(sa.Column("audio_seconds", sa.Float(), nullable=False, server_default="0"))
    with op.batch_alter_table("local_tts_generations") as b:
        b.add_column(sa.Column("parent_job_id", sa.String(32)))
        b.create_index("ix_local_tts_generations_parent_job_id", ["parent_job_id"])
        b.add_column(sa.Column("segment_index", sa.Integer()))
        for name in ["started_at", "completed_at", "first_audio_at"]:
            b.add_column(sa.Column(name, sa.Float()))
        b.add_column(sa.Column("stream_events", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("local_tts_worker") as b:
        b.add_column(sa.Column("engine", sa.String(40), nullable=False, server_default="qwen3"))
        b.add_column(sa.Column("cuda_graphs", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("load_seconds", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("warmup_seconds", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Restore the pre-0006 backup to retain measured progress history")
