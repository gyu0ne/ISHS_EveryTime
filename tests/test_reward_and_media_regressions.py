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
TEMPLATE_MAIN_NOTLOGINED = (Path(__file__).resolve().parents[1] / "templates" / "main_notlogined.html").read_text(encoding="utf-8")
TEMPLATE_POST_EDIT_GUEST = (Path(__file__).resolve().parents[1] / "templates" / "post_edit_guest.html").read_text(encoding="utf-8")
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
    def test_level_helpers_use_mild_rpg_curve_and_5_level_reward_steps(self):
        env = load_functions(
            ["get_required_exp_for_level", "get_level_point_reward"],
            {
                "math": __import__("math"),
                "BASE_EXP_PER_LEVEL": get_top_level_literal("BASE_EXP_PER_LEVEL"),
                "LEVEL_EXP_GROWTH_RATE": get_top_level_literal("LEVEL_EXP_GROWTH_RATE"),
                "BASE_LEVEL_UP_POINT_REWARD": get_top_level_literal("BASE_LEVEL_UP_POINT_REWARD"),
                "LEVEL_REWARD_STEP": get_top_level_literal("LEVEL_REWARD_STEP"),
                "LEVEL_REWARD_STEP_INTERVAL": get_top_level_literal("LEVEL_REWARD_STEP_INTERVAL"),
            },
        )

        self.assertEqual(env["get_required_exp_for_level"](1), 500)
        self.assertEqual(env["get_required_exp_for_level"](2), 560)
        self.assertEqual(env["get_required_exp_for_level"](6), 881)
        self.assertEqual(env["get_level_point_reward"](2), 100)
        self.assertEqual(env["get_level_point_reward"](6), 120)
        self.assertEqual(env["get_level_point_reward"](11), 140)

    def test_level_up_awards_points_for_each_gained_level(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (login_id TEXT PRIMARY KEY, level INTEGER, exp INTEGER, point INTEGER)")
        conn.execute("INSERT INTO users (login_id, level, exp, point) VALUES (?, ?, ?, ?)", ("user-1", 1, 450, 50))

        env = load_functions(
            ["get_required_exp_for_level", "get_level_point_reward", "update_exp_level"],
            {
                "get_db": lambda: conn,
                "math": __import__("math"),
                "BASE_EXP_PER_LEVEL": get_top_level_literal("BASE_EXP_PER_LEVEL"),
                "LEVEL_EXP_GROWTH_RATE": get_top_level_literal("LEVEL_EXP_GROWTH_RATE"),
                "BASE_LEVEL_UP_POINT_REWARD": get_top_level_literal("BASE_LEVEL_UP_POINT_REWARD"),
                "LEVEL_REWARD_STEP": get_top_level_literal("LEVEL_REWARD_STEP"),
                "LEVEL_REWARD_STEP_INTERVAL": get_top_level_literal("LEVEL_REWARD_STEP_INTERVAL"),
            },
        )

        result = env["update_exp_level"]("user-1", 700)

        self.assertEqual(result["level_gained"], 2)
        self.assertEqual(result["point_reward"], 200)
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["exp"], 90)
        row = conn.execute("SELECT level, exp, point FROM users WHERE login_id = ?", ("user-1",)).fetchone()
        self.assertEqual(row, (3, 90, 250))

    def test_level_down_uses_previous_level_requirement_and_never_claws_back_points(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (login_id TEXT PRIMARY KEY, level INTEGER, exp INTEGER, point INTEGER)")
        conn.execute("INSERT INTO users (login_id, level, exp, point) VALUES (?, ?, ?, ?)", ("user-2", 3, 10, 25))

        env = load_functions(
            ["get_required_exp_for_level", "get_level_point_reward", "update_exp_level"],
            {
                "get_db": lambda: conn,
                "math": __import__("math"),
                "BASE_EXP_PER_LEVEL": get_top_level_literal("BASE_EXP_PER_LEVEL"),
                "LEVEL_EXP_GROWTH_RATE": get_top_level_literal("LEVEL_EXP_GROWTH_RATE"),
                "BASE_LEVEL_UP_POINT_REWARD": get_top_level_literal("BASE_LEVEL_UP_POINT_REWARD"),
                "LEVEL_REWARD_STEP": get_top_level_literal("LEVEL_REWARD_STEP"),
                "LEVEL_REWARD_STEP_INTERVAL": get_top_level_literal("LEVEL_REWARD_STEP_INTERVAL"),
            },
        )

        result = env["update_exp_level"]("user-2", -100)

        self.assertEqual(result["level_gained"], 0)
        self.assertEqual(result["point_reward"], 0)
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["exp"], 470)
        row = conn.execute("SELECT level, exp, point FROM users WHERE login_id = ?", ("user-2",)).fetchone()
        self.assertEqual(row, (2, 470, 25))

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

    def test_normalize_rich_media_tags_adds_noopener_to_blank_links(self):
        env = load_functions(
            ["set_html_tag_attr", "normalize_rich_media_tags"],
            {
                "html": html,
                "re": __import__("re"),
                "urlparse": urlparse,
                "TRUSTED_IFRAME_HOSTS": get_top_level_literal("TRUSTED_IFRAME_HOSTS"),
            },
        )

        normalized = env["normalize_rich_media_tags"](
            '<a href="https://example.com" target="_blank">link</a>'
        )

        self.assertIn('target="_blank"', normalized)
        self.assertIn('rel="noopener noreferrer"', normalized)

    def test_post_content_allowed_tags_exclude_iframes(self):
        self.assertNotIn("iframe", get_top_level_literal("RICH_CONTENT_ALLOWED_TAGS"))

    def test_post_write_script_removes_iframes_before_submit(self):
        self.assertIn("find('iframe').remove()", POST_WRITE_JS)
        self.assertIn("iframe", POST_WRITE_JS)

    def test_editor_toolbars_do_not_expose_video_iframe_insertion(self):
        self.assertNotIn("'video'", POST_WRITE_JS)
        self.assertNotIn("'video'", TEMPLATE_POST_EDIT_GUEST)

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
        self.assertIn("https://www.googletagmanager.com", secured.headers["Content-Security-Policy"])
        self.assertIn("https://static.cloudflareinsights.com", secured.headers["Content-Security-Policy"])
        self.assertIn("https://cdn.jsdelivr.net", secured.headers["Content-Security-Policy"])
        self.assertEqual(secured.headers["Strict-Transport-Security"], "max-age=31536000; includeSubDomains")
        self.assertTrue(secured.cache_control.public)
        self.assertEqual(secured.cache_control.max_age, 31536000)
        self.assertTrue(secured.cache_control.immutable)

    def test_etacon_limit_constant_and_template_copy_match_100(self):
        self.assertEqual(get_top_level_literal("MAX_ETACONS_PER_PACK"), 100)
        self.assertIn("1팩당 최대 100개", TEMPLATE_REQUEST)
        self.assertIn("const MAX_ETACON_FILES = 100;", TEMPLATE_REQUEST)
        self.assertIn("this.files.length > MAX_ETACON_FILES", TEMPLATE_REQUEST)

    def test_etacon_detail_images_use_async_decoding(self):
        self.assertGreaterEqual(TEMPLATE_SHOP.count('decoding="async"'), 2)

    def test_anonymous_meal_loader_uses_text_nodes_with_br_allowlist(self):
        self.assertIn("function setMealText", TEMPLATE_MAIN_NOTLOGINED)
        self.assertIn("document.createTextNode(part)", TEMPLATE_MAIN_NOTLOGINED)
        self.assertIn("document.createElement('br')", TEMPLATE_MAIN_NOTLOGINED)
        self.assertNotIn("innerHTML", TEMPLATE_MAIN_NOTLOGINED)

    def test_frontend_media_paths_keep_lazy_loading_optimizations(self):
        self.assertIn("img.loading = index < 2 ? 'eager' : 'lazy';", TEMPLATE_BASE)
        self.assertIn("iframe.loading = 'lazy';", TEMPLATE_BASE)
        self.assertIn("this.loading = 'lazy';", POST_WRITE_JS)
        self.assertIn("this.decoding = 'async';", POST_WRITE_JS)
        self.assertIn("const MAX_TOTAL_IMAGE_SIZE_BYTES = MAX_TOTAL_IMAGE_SIZE_MB * 1000 * 1000;", POST_WRITE_JS)
        self.assertIn("function bytesToMB(bytes)", POST_WRITE_JS)
        self.assertIn('loading="lazy" decoding="async"', TEMPLATE_SHOP)


if __name__ == "__main__":
    unittest.main()
