import argparse
import getpass
from sqlalchemy import select
from .models import DB, User
from .security import passwords


def main():
    parser = argparse.ArgumentParser(description="Create or reset a SUMX administrator")
    parser.add_argument("email")
    args = parser.parse_args()
    password = getpass.getpass("Admin password (12+ characters): ")
    if len(password) < 12 or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters")
    with DB() as db:
        email = args.email.strip().lower()
        if "@" not in email:
            raise SystemExit("A valid email is required")
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, name="Administrator", role="admin")
            db.add(user)
        user.role = "admin"
        user.password_hash = passwords.hash(password)
        db.commit()
    print("Administrator saved. Use /admin/login.")


if __name__ == "__main__":
    main()
