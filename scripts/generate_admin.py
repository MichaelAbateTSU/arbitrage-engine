"""Run interactively; outputs a hash, never the password."""

import getpass
import secrets

from argon2 import PasswordHasher

password = getpass.getpass("Choose operator password (minimum 16 characters): ")
if len(password) < 16:
    raise SystemExit("Password must be at least 16 characters")
if getpass.getpass("Confirm password: ") != password:
    raise SystemExit("Passwords do not match")
print("ADMIN_PASSWORD_HASH='" + PasswordHasher().hash(password) + "'")
print("SESSION_SECRET='" + secrets.token_urlsafe(48) + "'")
