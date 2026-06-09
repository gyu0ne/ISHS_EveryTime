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
            ["normalize_riro_identity_value", "apply_riro_reauth_result"],
            {
                "sqlite3": sqlite3,
                "get_db": lambda: conn,
                "add_log": lambda action, user_id, details: logs.append((action, user_id, details)),
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
            ["normalize_riro_identity_value", "apply_riro_reauth_result"],
            {
                "sqlite3": sqlite3,
                "get_db": lambda: conn,
                "add_log": lambda action, user_id, details: logs.append((action, user_id, details)),
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


if __name__ == "__main__":
    unittest.main()
