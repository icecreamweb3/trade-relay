#!/usr/bin/env python3
"""
Hash a plaintext password using bcrypt (same algorithm as the application).

Usage:
    python scripts/hash_password.py
    python scripts/hash_password.py <password>

The resulting hash can be inserted directly into the users.password_hash column.
"""
import sys
import getpass
import bcrypt


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def main() -> None:
    if len(sys.argv) >= 2:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Enter password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)

    if not password:
        print("Error: password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    print(hash_password(password))


if __name__ == "__main__":
    main()
