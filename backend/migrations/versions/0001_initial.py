"""Initial SUMX schema. Immutable migration metadata is stored alongside this file."""

import json
from pathlib import Path
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    schema = json.loads(Path(__file__).with_name("0001_schema.json").read_text())
    for table in schema:
        columns = []
        for c in table["columns"]:
            typ = {
                "String": lambda: sa.String(c["length"]),
                "Text": sa.Text,
                "Integer": sa.Integer,
                "Float": sa.Float,
                "Boolean": sa.Boolean,
                "JSON": sa.JSON,
            }[c["type"]]()
            args = [sa.ForeignKey(c["foreign_key"])] if c.get("foreign_key") else []
            columns.append(
                sa.Column(c["name"], typ, *args, primary_key=c["primary_key"], nullable=c["nullable"])
            )
        constraints = [sa.UniqueConstraint(*cols) for cols in table["unique"]]
        op.create_table(table["name"], *columns, *constraints)
        for index in table["indexes"]:
            op.create_index(index["name"], table["name"], index["columns"], unique=index["unique"])


def downgrade():
    schema = json.loads(Path(__file__).with_name("0001_schema.json").read_text())
    for table in reversed(schema):
        op.drop_table(table["name"])
