"""Local GPU speech metadata; preserve old media, consent, profiles and billed history."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"


def upgrade():
    op.add_column(
        "users", sa.Column("tts_preset_voice", sa.String(64), nullable=False, server_default="Aiden")
    )
    with op.batch_alter_table("cloned_voices") as b:
        b.add_column(sa.Column("reference_verified_at", sa.Float()))
        b.add_column(sa.Column("reference_quality", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("preview_version", sa.String(80)))
    with op.batch_alter_table("local_tts_generations") as b:
        b.add_column(sa.Column("voice_type", sa.String(16), nullable=False, server_default="cloned"))
        b.add_column(sa.Column("preset_voice", sa.String(64)))
        b.add_column(sa.Column("diagnostics", sa.JSON(), nullable=False, server_default="{}"))
    # Never resume a partially generated segment under a different engine/model.
    op.execute(
        "UPDATE local_tts_generations SET state='failed',stage='failed',error='Speech engine migrated. Generate a new preview or narration.' WHERE state IN ('running','queued')"
    )
    op.execute(
        "UPDATE jobs SET state='failed',error='Speech engine migrated. Start a new narration.',narration_stage='FAILED' WHERE kind='speech' AND state IN ('running','queued')"
    )
    op.execute("UPDATE cloned_voices SET preview_until=0")


def downgrade():
    raise RuntimeError("Restore the pre-0009 backup to preserve speech provenance")
