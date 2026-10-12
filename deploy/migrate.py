"""Migrate and secure the historical default administrator before web startup."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

subprocess.run([sys.executable, "-m", "flask", "--app", "run:app", "db", "upgrade"], check=True)
from run import app
from app.extensions import db
from app.users.models import User

with app.app_context():
    admin = User.query.filter_by(email="superadmin").one_or_none()
    if admin is not None:
        hasher = PasswordHasher()
        try:
            uses_default = hasher.verify(admin.password_hash, "superadmin")
        except VerifyMismatchError:
            uses_default = False
        if uses_default:
            password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
            if len(password) < 24 or password.startswith("REPLACE_") or password == "superadmin":
                raise SystemExit("Set BOOTSTRAP_ADMIN_PASSWORD to a random password of at least 24 characters before initial deployment")
            admin.password_hash = hasher.hash(password)
            db.session.commit()
            print("Initial administrator password secured.")
