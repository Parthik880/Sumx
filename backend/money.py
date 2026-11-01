from decimal import Decimal, InvalidOperation
from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator

ZERO = Decimal("0")
PRECISION = Decimal("0.000000000001")


def usd(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number >= Decimal("1000000000000"):
            return None
        return number.quantize(PRECISION)
    except (InvalidOperation, ValueError):
        return None


class USD(TypeDecorator):
    """SQLite stores exact decimal text; PostgreSQL uses fixed-point NUMERIC."""

    impl = Numeric(24, 12)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(40) if dialect.name == "sqlite" else Numeric(24, 12))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        number = usd(value)
        if number is None:
            raise ValueError("Invalid USD amount")
        return format(number, "f") if dialect.name == "sqlite" else number

    def process_result_value(self, value, dialect):
        return Decimal(str(value)) if value is not None else None


class DecimalSum:
    def __init__(self):
        self.total = ZERO

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))

    def finalize(self):
        return str(self.total)
