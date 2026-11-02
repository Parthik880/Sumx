"""Assign older summaries to their mode only where job history proves it.

Each completed pipeline or summarize job produced one summary, in order. When those counts match,
the mapping is certain. Otherwise rows stay blank: nothing is guessed, copied or deleted.
Only blank rows are touched, so running this again changes nothing.
"""

import json
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"


def upgrade():
    bind = op.get_bind()
    meetings = bind.execute(sa.text("SELECT id, detail FROM meetings")).all()
    for meeting_id, default in meetings:
        rows = bind.execute(
            sa.text("SELECT id, detail FROM summaries WHERE meeting_id = :m ORDER BY version"),
            {"m": meeting_id},
        ).all()
        jobs = bind.execute(
            sa.text(
                "SELECT payload FROM jobs WHERE meeting_id = :m AND state = 'completed' "
                "AND kind IN ('pipeline', 'summarize') ORDER BY created"
            ),
            {"m": meeting_id},
        ).all()
        if not rows or len(rows) != len(jobs):
            continue
        for (summary_id, detail), (payload,) in zip(rows, jobs):
            data = json.loads(payload) if isinstance(payload, str) else payload
            mode = (data or {}).get("detail") or default
            if detail is None and mode in ("concise", "standard", "detailed"):
                bind.execute(
                    sa.text("UPDATE summaries SET detail = :d WHERE id = :i AND detail IS NULL"),
                    {"d": mode, "i": summary_id},
                )
    with op.batch_alter_table("summaries") as b:
        b.create_index("ix_summaries_meeting_detail_version", ["meeting_id", "detail", "version"])


def downgrade():
    with op.batch_alter_table("summaries") as b:
        b.drop_index("ix_summaries_meeting_detail_version")
