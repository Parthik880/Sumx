"""Local GPU work and metrics, preserving existing reference audio and narration."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def upgrade():
    with op.batch_alter_table("cloned_voices") as b:
        # Existing speech is not assumed to match the displayed paragraph.
        b.add_column(sa.Column("reference_text", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "local_tts_generations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_key", sa.String(200), nullable=False, unique=True),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("voice_id", sa.String(32), nullable=False),
        sa.Column("meeting_id", sa.String(32)),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reference_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_characters", sa.Integer(), nullable=False),
        sa.Column("audio_seconds", sa.Float(), nullable=False),
        sa.Column("processing_seconds", sa.Float(), nullable=False),
        sa.Column("peak_vram_mb", sa.Float(), nullable=False),
        sa.Column(
            "external_api_cost",
            sa.String(40) if op.get_bind().dialect.name == "sqlite" else sa.Numeric(24, 12),
            nullable=False,
        ),
        sa.Column("output_path", sa.Text()),
        sa.Column("created", sa.Float(), nullable=False),
        sa.Column("heartbeat", sa.Float(), nullable=False),
    )
    for name in ["user_id", "voice_id", "meeting_id", "state"]:
        op.create_index("ix_local_tts_generations_" + name, "local_tts_generations", [name])
    op.create_table(
        "local_tts_worker",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("gpu_name", sa.String(200)),
        sa.Column("vram_mb", sa.Float(), nullable=False),
        sa.Column("peak_vram_mb", sa.Float(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("heartbeat", sa.Float(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore the pre-Qwen backup to preserve transcripts and local generation history")
