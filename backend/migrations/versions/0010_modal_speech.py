"""Remember each Modal call so a restarted queue worker resumes it instead of paying twice."""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"


def upgrade():
    with op.batch_alter_table("local_tts_generations") as b:
        b.add_column(sa.Column("remote_call_id", sa.String(80)))


def downgrade():
    with op.batch_alter_table("local_tts_generations") as b:
        b.drop_column("remote_call_id")
