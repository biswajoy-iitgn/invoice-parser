"""Sign-in, sessions and roles.

People sign in with an e-mail and a password; the service sets a session cookie (HttpOnly, SameSite=Strict, Secure over
HTTPS) holding a random token, and keeps only the token's SHA-256. Every API call is checked against the session's role on
the server; the UI's own role is only what it shows.

Storage is one SQLite file (AUTH_DB, default work/auth.sqlite3, owner-only): the app runs with or without its PostgreSQL
database, and sign-in must work either way. Passwords are hashed with scrypt (stdlib). Five failed sign-ins lock an account
for 15 minutes. Sessions end after SESSION_IDLE_HOURS without use (default 8) and SESSION_MAX_HOURS in any case (default 72).

The first administrator is created on this machine:
    .venv/bin/python -m invoice_extractor.auth create-admin you@example.com "Your Name"
or once through POST /api/auth/setup from the machine itself while no one exists yet.

AUTH=off switches all of this off (tests, a laptop demo); the service logs a warning at every start when it is off.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("user", "admin")
IDLE_S = float(os.environ.get("SESSION_IDLE_HOURS", "8")) * 3600
MAX_S = float(os.environ.get("SESSION_MAX_HOURS", "72")) * 3600
LOCK_AFTER, LOCK_S = 5, 15 * 60
COOKIE = "dx_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("AUTH", "on").lower() not in ("off", "0", "false")


def db_path() -> Path:
    return Path(os.environ.get("AUTH_DB") or ROOT / "work" / "auth.sqlite3")


@contextmanager
def _db():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    if fresh:
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))      # owner-only from the first byte
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin')), password TEXT NOT NULL,
                must_change INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
                created REAL NOT NULL, last_login REAL);
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created REAL NOT NULL, seen REAL NOT NULL);""")
        yield con
    finally:
        con.close()


# ------------------------------------------------------------- passwords --
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, n, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=8, p=1, dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def password_problem(pw: str) -> str:
    """Why a new password is refused, else "" (length over composition rules, as NIST 800-63B advises)."""
    if len(pw) < 12:
        return "use at least 12 characters"
    if len(pw) > 256:
        return "use at most 256 characters"
    if pw.lower() in ("password1234", "123456789012", "qwertyuiop12"):
        return "that password is too common"
    return ""


# ------------------------------------------------------------------ users --
def _public(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"], "must_change": bool(row["must_change"]),
            "disabled": bool(row["disabled"]), "last_login": row["last_login"]}


def count_users() -> int:
    with _db() as con:
        return con.execute("SELECT count(*) FROM users").fetchone()[0]


def create_user(email: str, name: str, role: str, password: str, must_change: bool = True) -> dict:
    email, name = email.strip(), name.strip()
    if "@" not in email or len(email) > 254 or not name or len(name) > 120:
        raise ValueError("give an e-mail address and a name")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if why := password_problem(password):
        raise ValueError(f"password: {why}")
    uid = secrets.token_hex(16)
    with _lock, _db() as con:
        try:
            con.execute("INSERT INTO users (id, email, name, role, password, must_change, created) VALUES (?,?,?,?,?,?,?)",
                        (uid, email, name, role, hash_password(password), int(must_change), time.time()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("someone with that e-mail already exists") from exc
        return _public(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def list_users() -> list[dict]:
    with _db() as con:
        return [_public(r) for r in con.execute("SELECT * FROM users ORDER BY name")]


def update_user(uid: str, by: dict, role: str | None = None, disabled: bool | None = None, name: str | None = None) -> dict:
    with _lock, _db() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        if uid == by["id"] and (role not in (None, row["role"]) or disabled):
            raise ValueError("you can't change your own role or switch yourself off")
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        admins = con.execute("SELECT count(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0]
        if row["role"] == "admin" and (role == "user" or disabled) and admins <= 1:
            raise ValueError("keep at least one active administrator")
        con.execute("UPDATE users SET role = coalesce(?, role), disabled = coalesce(?, disabled), name = coalesce(?, name) WHERE id = ?",
                    (role, None if disabled is None else int(disabled), name.strip() if name else None, uid))
        if disabled:
            con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        return _public(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def reset_password(uid: str, password: str, must_change: bool = True) -> None:
    if why := password_problem(password):
        raise ValueError(f"password: {why}")
    with _lock, _db() as con:
        if con.execute("UPDATE users SET password = ?, must_change = ?, failed = 0, locked_until = 0 WHERE id = ?",
                       (hash_password(password), int(must_change), uid)).rowcount == 0:
            raise KeyError(uid)
        con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))          # every device signs in again


# --------------------------------------------------------------- sessions --
def login(email: str, password: str) -> tuple[str, dict]:
    """(session token, user). Raises PermissionError with a message that never says which part was wrong."""
    now = time.time()
    with _lock, _db() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email.strip(),)).fetchone()
        if row is None:
            check_password(password, hash_password("x" * 12))              # same work either way: no account probing by timing
            raise PermissionError("wrong e-mail or password")
        if row["locked_until"] > now:
            raise PermissionError(f"too many failed sign-ins; try again in {int((row['locked_until'] - now) // 60) + 1} min")
        if not check_password(password, row["password"]):
            failed = row["failed"] + 1
            con.execute("UPDATE users SET failed = ?, locked_until = ? WHERE id = ?",
                        (0 if failed >= LOCK_AFTER else failed, now + LOCK_S if failed >= LOCK_AFTER else 0, row["id"]))
            raise PermissionError("wrong e-mail or password")
        if row["disabled"]:
            raise PermissionError("this account is switched off; ask an administrator")
        token = secrets.token_urlsafe(32)
        con.execute("UPDATE users SET failed = 0, locked_until = 0, last_login = ? WHERE id = ?", (now, row["id"]))
        con.execute("INSERT INTO sessions (token, user_id, created, seen) VALUES (?,?,?,?)", (_h(token), row["id"], now, now))
        con.execute("DELETE FROM sessions WHERE seen < ? OR created < ?", (now - IDLE_S, now - MAX_S))
        return token, _public(row)


def user_for(token: str | None) -> dict | None:
    """The signed-in user for a session token, or None (expired, signed out, switched off)."""
    if not token:
        return None
    now = time.time()
    with _db() as con:
        row = con.execute("""SELECT u.*, s.created AS s_created, s.seen AS s_seen FROM sessions s JOIN users u ON u.id = s.user_id
                              WHERE s.token = ?""", (_h(token),)).fetchone()
        if row is None or row["disabled"] or now - row["s_seen"] > IDLE_S or now - row["s_created"] > MAX_S:
            return None
        if now - row["s_seen"] > 60:                                         # one write a minute at most
            con.execute("UPDATE sessions SET seen = ? WHERE token = ?", (now, _h(token)))
        return _public(row)


def logout(token: str | None) -> None:
    if token:
        with _db() as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (_h(token),))


def change_password(user: dict, old: str, new: str) -> None:
    with _db() as con:
        row = con.execute("SELECT password FROM users WHERE id = ?", (user["id"],)).fetchone()
    if row is None or not check_password(old, row["password"]):
        raise PermissionError("the current password is wrong")
    reset_password(user["id"], new, must_change=False)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def main(argv: list[str]) -> int:
    import getpass

    if len(argv) >= 3 and argv[0] == "create-admin":
        pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (12+ characters): ")
        try:
            u = create_user(argv[1], " ".join(argv[2:]), "admin", pw, must_change=False)
        except ValueError as exc:
            print(f"not created: {exc}", file=sys.stderr)
            return 1
        print(f"administrator {u['email']} created ({db_path()})")
        return 0
    print('usage: python -m invoice_extractor.auth create-admin EMAIL "NAME"', file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
