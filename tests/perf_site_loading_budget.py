import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
POST_WRITE_JS = ROOT / "static" / "js" / "post_write.js"
POST_EDIT_GUEST_TEMPLATE = TEMPLATES_DIR / "post_edit_guest.html"

SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=(['\"])(.*?)\1", re.IGNORECASE)
STYLESHEET_HREF_RE = re.compile(
    r"<link\b(?=[^>]*\brel=(['\"])stylesheet\1)[^>]*\bhref=(['\"])(.*?)\2",
    re.IGNORECASE,
)
MACHINE_LOCAL_ASSET_RE = re.compile(
    r"\b(?:src|href)=(['\"])(?:[A-Za-z]:\\|file://|/mnt/[A-Za-z]/).*?\1",
    re.IGNORECASE,
)


def normalize_asset_url(url):
    return url.strip()


def find_template_issues():
    issues = []

    for template_path in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel_path = template_path.relative_to(ROOT)
        text = template_path.read_text(encoding="utf-8")

        script_urls = [normalize_asset_url(match.group(2)) for match in SCRIPT_SRC_RE.finditer(text)]
        stylesheet_urls = [normalize_asset_url(match.group(3)) for match in STYLESHEET_HREF_RE.finditer(text)]

        for url in script_urls:
            if not url:
                issues.append(f"{rel_path}: empty <script src> creates an avoidable page request")

        for match in MACHINE_LOCAL_ASSET_RE.finditer(text):
            issues.append(f"{rel_path}: machine-local asset path is not loadable in production: {match.group(0)}")

        for kind, urls in (("script", script_urls), ("stylesheet", stylesheet_urls)):
            counts = Counter(url for url in urls if url)
            for url, count in sorted(counts.items()):
                if count > 1:
                    issues.append(
                        f"{rel_path}: duplicate {kind} request x{count}: {url}"
                    )

    return issues


def find_editor_issues():
    issues = []
    post_write_js = POST_WRITE_JS.read_text(encoding="utf-8")
    post_edit_guest = POST_EDIT_GUEST_TEMPLATE.read_text(encoding="utf-8")

    if "find('iframe').remove()" not in post_write_js:
        issues.append("static/js/post_write.js: iframes must be removed before editor submit")

    if "'video'" in post_write_js:
        issues.append("static/js/post_write.js: Summernote video insertion must stay disabled")

    if "'video'" in post_edit_guest:
        issues.append("templates/post_edit_guest.html: inline guest editor video insertion must stay disabled")

    return issues


def main():
    issues = []
    issues.extend(find_template_issues())
    issues.extend(find_editor_issues())

    if issues:
        print("Performance budget failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("Performance budget passed: no avoidable empty/duplicate/local asset requests and editor iframe insertion is blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
