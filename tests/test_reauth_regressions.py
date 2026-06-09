import ast
import sqlite3
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE, filename=str(APP_PATH))
MYPAGE_TEMPLATE = (Path(__file__).resolve().parents[1] / "templates" / "my_page.html").read_text(encoding="utf-8")


def load_functions(function_names, extra_globals=None):
    env = {"__builtins__": __builtins__}
    if extra_globals:
        env.update(extra_globals)

    wanted = set(function_names)
    for node in APP_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, filename=str(APP_PATH), mode="exec"), env)
    return env


class ReauthRegressionTests(unittest.TestCase):
    def make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE users (
                login_id TEXT PRIMARY KEY, name TEXT, hakbun INTEGER, gen INTEGER,
                status TEXT DEFAULT 'active'
            )
        """)
        conn.execute("INSERT INTO users (login_id, name, hakbun, gen, status) VALUES (?, ?, ?, ?, ?)", ("current", "홍길동", 2305, 30, "active"))
        conn.execute("INSERT INTO users (login_id, name, hakbun, gen, status) VALUES (?, ?, ?, ?, ?)", ("other", "다른학생", 2407, 31, "active"))
        return conn

    def test_apply_riro_reauth_result_updates_hakbun_and_gen_with_new_auth_wins(self):
        conn = self.make_conn()
        logs = []
        env = load_functions(
            ["ensure_riro_reauth_tracking", "normalize_riro_identity_value", "apply_riro_reauth_result"],
            {
                "sqlite3": sqlite3,
                "get_db": lambda: conn,
                "add_log": lambda action, user_id, details: logs.append((action, user_id, details)),
                "datetime": __import__("datetime"),
            },
        )

        result = env["apply_riro_reauth_result"](
            "current",
            {"status": "success", "name": "홍길동", "student_number": "2407", "generation": 31},
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["collision_detected"])
        current = conn.execute("SELECT hakbun, gen FROM users WHERE login_id = ?", ("current",)).fetchone()
        self.assertEqual((current["hakbun"], current["gen"]), (2407, 31))
        self.assertTrue(any(action == "RIRO_REAUTH_UPDATE" for action, _, _ in logs))
        self.assertTrue(any(action == "RIRO_REAUTH_HAKBUN_COLLISION" for action, _, _ in logs))

    def test_apply_riro_reauth_result_rejects_name_mismatch_without_update(self):
        conn = self.make_conn()
        logs = []
        env = load_functions(
            ["ensure_riro_reauth_tracking", "normalize_riro_identity_value", "apply_riro_reauth_result"],
            {
                "sqlite3": sqlite3,
                "get_db": lambda: conn,
                "add_log": lambda action, user_id, details: logs.append((action, user_id, details)),
                "datetime": __import__("datetime"),
            },
        )

        result = env["apply_riro_reauth_result"](
            "current",
            {"status": "success", "name": "김철수", "student_number": "2408", "generation": 31},
        )

        self.assertEqual(result["status"], "error")
        current = conn.execute("SELECT hakbun, gen FROM users WHERE login_id = ?", ("current",)).fetchone()
        self.assertEqual((current["hakbun"], current["gen"]), (2305, 30))
        self.assertTrue(any(action == "RIRO_REAUTH_NAME_MISMATCH" for action, _, _ in logs))

    def test_mypage_exposes_reauth_entrypoint(self):
        self.assertIn("riro_reauth", MYPAGE_TEMPLATE)
        self.assertIn("리로스쿨 재인증", MYPAGE_TEMPLATE)

    def test_reauth_tracking_migration_marks_existing_users_required(self):
        conn = self.make_conn()
        env = load_functions(
            ["ensure_riro_reauth_tracking"],
            {
                "get_db": lambda: conn,
            },
        )

        env["ensure_riro_reauth_tracking"]()

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        self.assertIn("riro_reauth_required", columns)
        self.assertIn("riro_reauth_at", columns)
        required = conn.execute(
            "SELECT riro_reauth_required FROM users WHERE login_id = ?",
            ("current",),
        ).fetchone()["riro_reauth_required"]
        self.assertEqual(required, 1)

    def test_successful_reauth_clears_required_flag_and_records_timestamp(self):
        conn = self.make_conn()
        logs = []
        env = load_functions(
            ["ensure_riro_reauth_tracking", "normalize_riro_identity_value", "apply_riro_reauth_result"],
            {
                "sqlite3": sqlite3,
                "get_db": lambda: conn,
                "add_log": lambda action, user_id, details: logs.append((action, user_id, details)),
                "datetime": __import__("datetime"),
            },
        )

        result = env["apply_riro_reauth_result"](
            "current",
            {"status": "success", "name": "홍길동", "student_number": "2407", "generation": 31},
        )

        self.assertEqual(result["status"], "success")
        row = conn.execute(
            "SELECT riro_reauth_required, riro_reauth_at FROM users WHERE login_id = ?",
            ("current",),
        ).fetchone()
        self.assertEqual(row["riro_reauth_required"], 0)
        self.assertIsNotNone(row["riro_reauth_at"])

    def test_stale_logged_in_user_gets_alert_redirect_to_riro_reauth(self):
        captured = {}

        class DummyResponse:
            def __init__(self, body, status=200):
                self.body = body
                self.status_code = status

        stale_user = {"login_id": "current", "riro_reauth_required": 1}
        request_state = type("Request", (), {
            "endpoint": "main",
            "path": "/",
            "method": "GET",
            "is_json": False,
        })()
        session_state = {}
        env = load_functions(
            [
                "user_requires_riro_reauth",
                "is_riro_reauth_exempt_request",
                "load_session_user_for_reauth_gate",
                "enforce_required_riro_reauth",
            ],
            {
                "g": type("G", (), {"user": stale_user})(),
                "request": request_state,
                "session": session_state,
                "Response": DummyResponse,
                "json": __import__("json"),
                "jsonify": lambda payload: captured.setdefault("json", payload),
                "url_for": lambda endpoint: "/riro-reauth" if endpoint == "riro_reauth" else f"/{endpoint}",
            },
        )

        response = env["enforce_required_riro_reauth"]()

        self.assertIsInstance(response, DummyResponse)
        self.assertIn("리로스쿨 재인증", response.body)
        self.assertIn("/riro-reauth", response.body)
        self.assertTrue(session_state["riro_reauth_forced"])

    def test_riro_reauth_route_is_exempt_from_forced_redirect_loop(self):
        request_state = type("Request", (), {
            "endpoint": "riro_reauth",
            "path": "/riro-reauth",
            "method": "GET",
            "is_json": False,
        })()
        env = load_functions(
            ["is_riro_reauth_exempt_request"],
            {
                "request": request_state,
            },
        )

        self.assertTrue(env["is_riro_reauth_exempt_request"]())


if __name__ == "__main__":
    unittest.main()
