"""Private reference voices and separate preview accounting; preserve existing audio."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"


def upgrade():
    op.create_table(
        "cloned_voices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference_path", sa.Text(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("preview_path", sa.Text()),
        sa.Column("consent_at", sa.Float(), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=False),
        sa.Column("preview_until", sa.Float(), nullable=False),
        sa.Column("preview_error", sa.Text()),
        sa.Column("created", sa.Float(), nullable=False),
        sa.Column("updated", sa.Float(), nullable=False),
    )
    op.create_index("ix_cloned_voices_user_id", "cloned_voices", ["user_id"])
    with op.batch_alter_table("audio_outputs") as b:
        b.add_column(sa.Column("voice_type", sa.String(16), nullable=False, server_default="preset"))
        b.add_column(sa.Column("voice_name", sa.String(100)))
    with op.batch_alter_table("api_usage") as b:
        b.alter_column("meeting_id", existing_type=sa.String(32), nullable=True)
        b.add_column(sa.Column("voice_type", sa.String(16)))
        b.add_column(sa.Column("input_characters", sa.Integer()))
        b.add_column(
            sa.Column("generation_status", sa.String(32), nullable=False, server_default="succeeded")
        )
        b.add_column(sa.Column("purpose", sa.String(24), nullable=False, server_default="meeting"))


def downgrade():
    # Preview usage has no meeting; retain it rather than silently discard accounting.
    raise RuntimeError("Restore a pre-0004 backup to downgrade without losing private voice/preview data")
