"""Keep invalidated session hashes long enough to identify revoked access."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade():
    op.add_column("sessions", sa.Column("revoked_at", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("revoked_at")
