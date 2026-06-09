import ast
import datetime
import json
import sqlite3
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE, filename=str(APP_PATH))
MAIN_LOGINED_TEMPLATE = (Path(__file__).resolve().parents[1] / "templates" / "main_logined.html").read_text(encoding="utf-8")


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


class TimetableRegressionTests(unittest.TestCase):
    def test_init_timetable_storage_creates_required_table(self):
        conn = sqlite3.connect(":memory:")
        env = load_functions(["init_timetable_storage"], {"get_db": lambda: conn})

        env["init_timetable_storage"]()

        row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'timetables'").fetchone()
        self.assertIsNotNone(row)

    def test_get_timetable_data_returns_cached_row_when_external_fetch_raises(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE timetables (
                grade INTEGER NOT NULL, class_num INTEGER NOT NULL,
                week_schedule TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (grade, class_num)
            )
        """)
        cached = {"월": [{"period": 1, "subject": "캐시수업"}]}
        conn.execute(
            "INSERT INTO timetables (grade, class_num, week_schedule, updated_at) VALUES (?, ?, ?, ?)",
            (2, 4, json.dumps(cached, ensure_ascii=False), "2000-01-01"),
        )

        class RaisingComciganAPI:
            def __init__(self, headless=True):
                pass

            def get_timetable(self, school_name, grade, class_num):
                raise RuntimeError("external down")

        logs = []
        env = load_functions(
            ["init_timetable_storage", "get_timetable_data"],
            {
                "get_db": lambda: conn,
                "datetime": datetime,
                "json": json,
                "ComciganAPI": RaisingComciganAPI,
                "add_log": lambda *args: logs.append(args),
            },
        )

        data = env["get_timetable_data"](2, 4)

        self.assertEqual(data, cached)
        self.assertTrue(any("시간표 수집 실패" in str(item) for item in logs))

    def test_get_timetable_data_creates_storage_and_fetches_when_empty(self):
        conn = sqlite3.connect(":memory:")

        class FakeComciganAPI:
            def __init__(self, headless=True):
                self.headless = headless

            def get_timetable(self, school_name, grade, class_num):
                return {"timetable": {"월": [{"period": 1, "subject": "수학"}]}}

        logs = []
        env = load_functions(
            ["init_timetable_storage", "get_timetable_data"],
            {
                "get_db": lambda: conn,
                "datetime": datetime,
                "json": json,
                "ComciganAPI": FakeComciganAPI,
                "add_log": lambda *args: logs.append(args),
            },
        )

        data = env["get_timetable_data"](2, 4)

        self.assertEqual(data["월"][0]["subject"], "수학")
        stored = conn.execute("SELECT week_schedule FROM timetables WHERE grade = ? AND class_num = ?", (2, 4)).fetchone()
        self.assertIsNotNone(stored)

    def test_logged_in_main_page_fetches_meals_with_cache(self):
        self.assertIn("/api/bob", MAIN_LOGINED_TEMPLATE)
        self.assertIn("loadMeals", MAIN_LOGINED_TEMPLATE)
        self.assertIn("localStorage", MAIN_LOGINED_TEMPLATE)

    def test_logged_in_main_page_fetches_timetable(self):
        self.assertIn("/api/timetable", MAIN_LOGINED_TEMPLATE)
        self.assertIn("timetable-list", MAIN_LOGINED_TEMPLATE)
        self.assertIn("loadTimetable", MAIN_LOGINED_TEMPLATE)
        self.assertIn("document.createElement('li')", MAIN_LOGINED_TEMPLATE)
        self.assertIn("subject.textContent", MAIN_LOGINED_TEMPLATE)
        self.assertNotIn("items.map((raw, index)", MAIN_LOGINED_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
