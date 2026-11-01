"""Preserve usage rows, exact known amounts and duplicate request audit history."""

from decimal import Decimal
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade():
    db = op.get_bind()
    with op.batch_alter_table("api_usage") as batch:
        batch.alter_column(
            "cost",
            existing_type=sa.Float(),
            type_=sa.String(40) if db.dialect.name == "sqlite" else sa.Numeric(24, 12),
        )
        batch.add_column(sa.Column("cost_status", sa.String(16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("billing_key", sa.String(250), nullable=True))
        batch.add_column(
            sa.Column("excluded_as_duplicate", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_index("ix_api_usage_billing_key", ["billing_key"], unique=True)
        batch.create_index("ix_api_usage_created", ["created"])
        batch.create_index("ix_api_usage_user_created", ["user_id", "created"])
    seen = {}
    # Existing unknown charges stay unknown. Historical duplicate rows remain auditable.
    rows = db.execute(
        sa.text("SELECT id,cost,cost_source,generation_id,request_id FROM api_usage ORDER BY created,id")
    ).mappings()
    for row in rows:
        key = (
            ("generation:" + row["generation_id"])
            if row["generation_id"]
            else ("request:" + row["request_id"])
            if row["request_id"]
            else None
        )
        duplicate = key in seen if key else False
        amount = (
            str(Decimal(str(row["cost"])).quantize(Decimal("0.000000000001")))
            if row["cost"] is not None
            else None
        )
        status = (
            "estimated"
            if amount is not None and "estimat" in row["cost_source"]
            else "actual"
            if amount is not None
            else "pending"
        )
        if duplicate and amount is not None and seen[key][1] == "pending":
            db.execute(
                sa.text(
                    "UPDATE api_usage SET cost=:cost,cost_status=:status,cost_source=:source WHERE id=:id"
                ),
                {"id": seen[key][0], "cost": amount, "status": status, "source": row["cost_source"]},
            )
            seen[key] = (seen[key][0], status)
        elif key and not duplicate:
            seen[key] = (row["id"], status)
        db.execute(
            sa.text(
                "UPDATE api_usage SET cost=:cost,cost_status=:status,billing_key=:key,excluded_as_duplicate=:duplicate WHERE id=:id"
            ),
            {
                "id": row["id"],
                "cost": amount,
                "status": status,
                "key": None if duplicate else key,
                "duplicate": duplicate,
            },
        )


def downgrade():
    with op.batch_alter_table("api_usage") as batch:
        batch.drop_index("ix_api_usage_created")
        batch.drop_index("ix_api_usage_user_created")
        batch.drop_index("ix_api_usage_billing_key")
        batch.drop_column("excluded_as_duplicate")
        batch.drop_column("billing_key")
        batch.drop_column("cost_status")
        batch.alter_column("cost", existing_type=sa.String(40), type_=sa.Float())
