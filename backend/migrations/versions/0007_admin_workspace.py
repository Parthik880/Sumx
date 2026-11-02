"""Separate admin identity, approval audit metadata and explicit usage currency."""

import uuid
import json
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    op.add_column("users", sa.Column("admin_id", sa.String(100)))
    op.add_column("users", sa.Column("picture", sa.Text()))
    op.add_column("users", sa.Column("last_login", sa.Float()))
    op.create_index("ix_users_admin_id", "users", ["admin_id"], unique=True)
    # Existing administrators retain their old email as the initial Admin ID.
    op.execute("UPDATE users SET admin_id=lower(email) WHERE role='admin'")
    with op.batch_alter_table("sessions") as b:
        b.add_column(sa.Column("auth_method", sa.String(24), nullable=False, server_default="google"))
    op.execute(
        "UPDATE sessions SET auth_method='admin_password' WHERE user_id IN (SELECT id FROM users WHERE role='admin')"
    )
    with op.batch_alter_table("allowed_emails") as b:
        b.add_column(sa.Column("id", sa.String(32)))
        b.add_column(sa.Column("approved_at", sa.Float()))
        b.add_column(sa.Column("approved_by", sa.String(32)))
        b.add_column(sa.Column("revoked_at", sa.Float()))
        b.create_foreign_key("fk_allowed_approved_by", "users", ["approved_by"], ["id"])
    db = op.get_bind()
    rows = db.execute(
        sa.text("SELECT a.email,u.id FROM allowed_emails a LEFT JOIN users u ON u.email=a.email")
    ).all()
    for email, user_id in rows:
        db.execute(
            sa.text("UPDATE allowed_emails SET id=:id WHERE email=:email"),
            {"id": user_id or uuid.uuid4().hex, "email": email},
        )
    with op.batch_alter_table("allowed_emails") as b:
        b.alter_column("id", existing_type=sa.String(32), nullable=False)
        b.create_unique_constraint("uq_allowed_emails_id", ["id"])
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor_id", sa.String(32), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(320)),
        sa.Column("created", sa.Float(), nullable=False),
    )
    op.create_index("ix_admin_audit_actor_id", "admin_audit", ["actor_id"])
    op.create_index("ix_admin_audit_created", "admin_audit", ["created"])
    with op.batch_alter_table("api_usage") as b:
        b.add_column(sa.Column("provider", sa.String(40), nullable=False, server_default="openrouter"))
        b.add_column(sa.Column("currency", sa.String(3), nullable=False, server_default="USD"))
        b.create_check_constraint("ck_api_usage_usd", "currency = 'USD'")
    # Local generations already persist their external API fee and measured compute usage.
    # Preserve those facts rather than assigning a hypothetical Modal price.
    for task in db.execute(sa.text("SELECT * FROM local_tts_generations")).mappings():
        terminal = task["state"] in ("completed", "failed")
        db.execute(
            sa.text("""INSERT INTO api_usage
            (id,user_id,meeting_id,operation,provider,currency,model,request_id,cost,cost_source,
             cost_status,billing_key,excluded_as_duplicate,units,voice_type,input_characters,
             generation_status,purpose,created)
            VALUES (:id,:user_id,:meeting_id,'tts','local_qwen3','USD',:model,:request_id,:cost,
                    :source,:status,:key,0,:units,'cloned',:characters,:state,:purpose,:created)"""),
            {
                "id": uuid.uuid4().hex,
                "user_id": task["user_id"],
                "meeting_id": task["meeting_id"],
                "model": task["model"],
                "request_id": task["id"],
                "cost": task["external_api_cost"] if terminal else None,
                "source": "local_no_external_api_fee" if terminal else "unavailable",
                "status": "actual" if terminal else "pending",
                "key": "local_qwen3:" + task["id"],
                "units": json.dumps(
                    {
                        "characters": task["input_characters"],
                        "seconds": task["audio_seconds"],
                        "processing_seconds": task["processing_seconds"],
                        "peak_vram_mb": task["peak_vram_mb"],
                    }
                ),
                "characters": task["input_characters"],
                "state": "succeeded" if task["state"] == "completed" else task["state"],
                "purpose": task["purpose"],
                "created": task["created"],
            },
        )


def downgrade():
    raise RuntimeError("Restore the pre-0007 backup to preserve admin audit records")
