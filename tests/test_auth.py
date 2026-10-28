"""Sign-in, sessions, roles and lockout, with sign-in switched on."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MODEL_POOL", "off")

from fastapi.testclient import TestClient  # noqa: E402

from invoice_extractor import auth, service  # noqa: E402

PW = "correct horse battery"


class SignIn(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.dict(os.environ, {"AUTH": "on", "AUTH_DB": str(Path(tmp.name, "auth.sqlite3"))}),
                  patch.object(service, "_LOGIN_TRIES", {})):
            p.start()
            self.addCleanup(p.stop)
        self.admin = auth.create_user("admin@example.com", "Admin", "admin", PW, must_change=False)
        self.c = TestClient(service.app)

    def login(self, email="admin@example.com", pw=PW, client=None):
        return (client or self.c).post("/api/auth/login", json={"email": email, "password": pw})

    def test_everything_but_the_shell_needs_a_session(self):
        self.assertEqual(self.c.get("/health").status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me").json()["user"], None)
        for path in ("/api/profiles", "/api/batches/x", "/uploads/abc.png", "/classic", "/api/users"):
            self.assertEqual(self.c.get(path).status_code, 401, path)
        r = self.login()
        self.assertEqual(r.status_code, 200)
        cookie = r.headers["set-cookie"].lower()
        for part in ("httponly", "samesite=strict", "path=/"):
            self.assertIn(part, cookie)
        self.assertEqual(self.c.get("/api/profiles").status_code, 200)
        self.c.post("/api/auth/logout")
        self.assertEqual(self.c.get("/api/profiles").status_code, 401)

    def test_roles_are_checked_on_the_server(self):
        self.login()
        r = self.c.post("/api/users", json={"email": "u@example.com", "name": "User", "role": "user", "password": "temporary pass 1"})
        self.assertEqual(r.status_code, 201)
        u = TestClient(service.app)
        self.login("u@example.com", "temporary pass 1", u)
        self.assertEqual(u.get("/api/profiles").status_code, 403)                  # a temporary password must be changed first
        self.assertEqual(u.post("/api/auth/password", json={"old": "temporary pass 1", "new": "my own long password"}).status_code, 200)
        self.assertEqual(u.get("/api/profiles").status_code, 200)
        for method, path in (("get", "/api/users"), ("get", "/api/db/status"), ("post", "/api/updates/apply"), ("put", "/api/profiles/general/models"), ("get", "/api/audit/entries")):
            self.assertEqual(getattr(u, method)(path).status_code, 403, path)
        self.assertEqual(self.c.patch(f"/api/users/{self.admin['id']}", json={"role": "user"}).status_code, 400)   # not yourself

    def test_wrong_passwords_lock_the_account_and_never_say_which_part_was_wrong(self):
        self.assertEqual(self.login(pw="wrong").json()["detail"], self.login("nobody@example.com").json()["detail"])
        for _ in range(auth.LOCK_AFTER - 1):
            self.login(pw="wrong")
        r = self.login()                                                            # right password, but locked now
        self.assertEqual(r.status_code, 401)
        self.assertIn("too many", r.json()["detail"])

    def test_passwords_are_hashed_and_short_ones_refused(self):
        with auth._db() as con:
            stored = con.execute("SELECT password FROM users").fetchone()[0]
        self.assertTrue(stored.startswith("scrypt$") and PW not in stored)
        with self.assertRaisesRegex(ValueError, "12 characters"):
            auth.create_user("x@example.com", "X", "user", "short")

    def test_first_administrator_only_from_this_machine_and_only_once(self):
        r = self.c.post("/api/auth/setup", json={"email": "b@example.com", "name": "B", "password": PW})
        self.assertEqual(r.status_code, 403)                                        # someone exists already


if __name__ == "__main__":
    unittest.main()
