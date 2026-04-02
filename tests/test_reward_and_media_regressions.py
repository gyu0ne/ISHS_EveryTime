import ast
import html
import sqlite3
import types
import unittest
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE, filename=str(APP_PATH))
TEMPLATE_REQUEST = (Path(__file__).resolve().parents[1] / "templates" / "etacon" / "request.html").read_text(encoding="utf-8")
TEMPLATE_SHOP = (Path(__file__).resolve().parents[1] / "templates" / "etacon" / "shop.html").read_text(encoding="utf-8")
TEMPLATE_BASE = (Path(__file__).resolve().parents[1] / "templates" / "base.html").read_text(encoding="utf-8")
POST_WRITE_JS = (Path(__file__).resolve().parents[1] / "static" / "js" / "post_write.js").read_text(encoding="utf-8")


def get_top_level_literal(name):
    for node in APP_TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError(name)


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


class DummyCacheControl:
    def __init__(self):
        self.public = False
        self.max_age = None
        self.immutable = False


class DummyResponse:
    def __init__(self, body="", status=200):
        self.body = body
        self.status_code = status
        self.headers = {}
        self.cache_control = DummyCacheControl()


class DummyJsonResponse(DummyResponse):
    def __init__(self, payload):
        super().__init__(body=payload, status=200)
        self.json = payload


class RewardAndMediaRegressionTests(unittest.TestCase):
    def test_level_up_awards_points_for_each_gained_level(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (login_id TEXT PRIMARY KEY, level INTEGER, exp INTEGER, point INTEGER)")
        conn.execute("INSERT INTO users (login_id, level, exp, point) VALUES (?, ?, ?, ?)", ("user-1", 2, 900, 50))

        env = load_functions(
            ["update_exp_level"],
            {
                "get_db": lambda: conn,
                "EXP_PER_LEVEL": get_top_level_literal("EXP_PER_LEVEL"),
                "LEVEL_UP_POINT_REWARD": get_top_level_literal("LEVEL_UP_POINT_REWARD"),
            },
        )

        result = env["update_exp_level"]("user-1", 2500)

        self.assertEqual(result["level_gained"], 3)
        self.assertEqual(result["point_reward"], 300)
        self.assertEqual(result["level"], 5)
        self.assertEqual(result["exp"], 400)
        row = conn.execute("SELECT level, exp, point FROM users WHERE login_id = ?", ("user-1",)).fetchone()
        self.assertEqual(row, (5, 400, 350))

    def test_level_down_never_drops_below_one_or_grants_points(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (login_id TEXT PRIMARY KEY, level INTEGER, exp INTEGER, point INTEGER)")
        conn.execute("INSERT INTO users (login_id, level, exp, point) VALUES (?, ?, ?, ?)", ("user-2", 1, 50, 25))

        env = load_functions(
            ["update_exp_level"],
            {
                "get_db": lambda: conn,
                "EXP_PER_LEVEL": get_top_level_literal("EXP_PER_LEVEL"),
                "LEVEL_UP_POINT_REWARD": get_top_level_literal("LEVEL_UP_POINT_REWARD"),
            },
        )

        result = env["update_exp_level"]("user-2", -200)

        self.assertEqual(result["level_gained"], 0)
        self.assertEqual(result["point_reward"], 0)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["exp"], 0)
        row = conn.execute("SELECT level, exp, point FROM users WHERE login_id = ?", ("user-2",)).fetchone()
        self.assertEqual(row, (1, 0, 25))

    def test_normalize_rich_media_tags_keeps_only_trusted_iframes_and_lazy_loads_media(self):
        env = load_functions(
            ["set_html_tag_attr", "normalize_rich_media_tags"],
            {
                "html": html,
                "re": __import__("re"),
                "urlparse": urlparse,
                "TRUSTED_IFRAME_HOSTS": get_top_level_literal("TRUSTED_IFRAME_HOSTS"),
            },
        )

        raw = (
            '<p>Hello</p>'
            '<img src="/static/a.png">'
            '<iframe src="https://www.youtube.com/embed/demo"></iframe>'
            '<iframe src="https://evil.example/embed/demo"></iframe>'
        )

        normalized = env["normalize_rich_media_tags"](raw)

        self.assertIn('loading="lazy"', normalized)
        self.assertIn('decoding="async"', normalized)
        self.assertIn('referrerpolicy="no-referrer"', normalized)
        self.assertIn('sandbox="allow-scripts allow-same-origin allow-presentation"', normalized)
        self.assertIn("youtube.com/embed/demo", normalized)
        self.assertNotIn("evil.example", normalized)

    def test_rate_limit_returns_retry_after_for_api_requests(self):
        class FakeTTLCache(dict):
            def __init__(self, maxsize, ttl):
                super().__init__()
                self.maxsize = maxsize
                self.ttl = ttl

        request_state = types.SimpleNamespace(method="POST", is_json=True, path="/api/test")

        def fake_jsonify(payload):
            return DummyJsonResponse(payload)

        env = load_functions(
            ["rate_limit"],
            {
                "TTLCache": FakeTTLCache,
                "wraps": wraps,
                "request": request_state,
                "get_client_identifier": lambda: "ip:test",
                "jsonify": fake_jsonify,
                "Response": DummyResponse,
            },
        )

        calls = []

        @env["rate_limit"](limit=2, window_seconds=60)
        def handler():
            calls.append("ok")
            return "ok"

        self.assertEqual(handler(), "ok")
        self.assertEqual(handler(), "ok")
        blocked = handler()

        self.assertEqual(calls, ["ok", "ok"])
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Retry-After"], "60")
        self.assertEqual(blocked.json["status"], "error")

    def test_apply_security_headers_sets_csp_hsts_and_static_cache_headers(self):
        request_state = types.SimpleNamespace(path="/static/images/demo.webp", is_secure=True)
        app_state = types.SimpleNamespace(
            config={"SEND_FILE_MAX_AGE_DEFAULT": 31536000},
            after_request=lambda fn: fn,
        )

        env = load_functions(
            ["apply_security_headers"],
            {
                "request": request_state,
                "app": app_state,
            },
        )

        response = DummyResponse()
        secured = env["apply_security_headers"](response)

        self.assertIs(secured, response)
        self.assertEqual(secured.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", secured.headers["Content-Security-Policy"])
        self.assertIn("https://www.youtube.com", secured.headers["Content-Security-Policy"])
        self.assertEqual(secured.headers["Strict-Transport-Security"], "max-age=31536000; includeSubDomains")
        self.assertTrue(secured.cache_control.public)
        self.assertEqual(secured.cache_control.max_age, 31536000)
        self.assertTrue(secured.cache_control.immutable)

    def test_etacon_limit_constant_and_template_copy_match_100(self):
        self.assertEqual(get_top_level_literal("MAX_ETACONS_PER_PACK"), 100)
        self.assertIn("1팩당 최대 100개", TEMPLATE_REQUEST)
        self.assertIn("const MAX_ETACON_FILES = 100;", TEMPLATE_REQUEST)
        self.assertIn("this.files.length > MAX_ETACON_FILES", TEMPLATE_REQUEST)

    def test_frontend_media_paths_keep_lazy_loading_optimizations(self):
        self.assertIn("img.loading = index < 2 ? 'eager' : 'lazy';", TEMPLATE_BASE)
        self.assertIn("iframe.loading = 'lazy';", TEMPLATE_BASE)
        self.assertIn("this.loading = 'lazy';", POST_WRITE_JS)
        self.assertIn("this.decoding = 'async';", POST_WRITE_JS)
        self.assertIn('loading="lazy" decoding="async"', TEMPLATE_SHOP)


if __name__ == "__main__":
    unittest.main()
