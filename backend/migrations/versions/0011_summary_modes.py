"""Record which mode and prompt produced a summary. Existing summaries stay as they are."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"


def upgrade():
    with op.batch_alter_table("summaries") as b:
        b.add_column(sa.Column("detail", sa.String(16)))
        b.add_column(sa.Column("prompt_version", sa.String(240)))


def downgrade():
    with op.batch_alter_table("summaries") as b:
        b.drop_column("prompt_version")
        b.drop_column("detail")
