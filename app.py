from flask import Flask, request, jsonify, send_from_directory
import requests
import re
import html
from urllib.parse import urlparse, urljoin, parse_qs, urlencode

app = Flask(__name__)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
})

def facebook_url(url):
    try:
        p = urlparse(url)
        h = (p.hostname or "").lower()
        return (
            p.scheme in ("http", "https")
            and (
                h == "facebook.com"
                or h.endswith(".facebook.com")
                or h == "fb.com"
                or h.endswith(".fb.com")
            )
        )
    except Exception:
        return False

def is_login_url(url):
    try:
        path = urlparse(url).path.lower()
        return path == "/login" or path.startswith("/login/")
    except Exception:
        return False

def clean_facebook_url(url):
    try:
        p = urlparse(url)
        qs = parse_qs(p.query)
        path = p.path.rstrip("/") or "/"

        if path.lower() == "/profile.php" and qs.get("id"):
            fb_id = qs["id"][0]
            if fb_id.isdigit():
                return "https://www.facebook.com/profile.php?" + urlencode({"id": fb_id})

        keep = {}
        for key in ("id", "story_fbid", "fbid", "v"):
            if key in qs and qs[key]:
                keep[key] = qs[key][0]

        base = "https://www.facebook.com" + path
        return base + ("?" + urlencode(keep) if keep else "")
    except Exception:
        return url

def try_people_numeric(url):
    try:
        if not urlparse(url).path.lower().startswith("/people/"):
            return None

        r = session.get(url, timeout=8, allow_redirects=True)

        if is_login_url(r.url):
            return None

        fp = urlparse(r.url)
        fq = parse_qs(fp.query)

        if (
            fp.path.rstrip("/").lower() == "/profile.php"
            and fq.get("id")
            and fq["id"][0].isdigit()
        ):
            return fq["id"][0]

        text = html.unescape(r.text)
        patterns = (
            r'"userID"\s*:\s*"(\d{8,25})"',
            r'"user_id"\s*:\s*"(\d{8,25})"',
            r'"profile_id"\s*:\s*"(\d{8,25})"',
            r'profile\.php\?id=(\d{8,25})'
        )
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)

    except requests.RequestException:
        pass
    return None

@app.get("/")
def index():
    return send_from_directory(".", "index.html")

@app.post("/api/resolve")
def resolve():
    data = request.get_json(silent=True) or {}
    original_url = data.get("url", "").strip()

    if not facebook_url(original_url):
        return jsonify(error="Colle un lien Facebook valide."), 400

    try:
        current = original_url

        for _ in range(5):
            r = session.get(current, allow_redirects=False, timeout=8)

            if not (r.is_redirect or r.is_permanent_redirect):
                break

            location = r.headers.get("Location")
            if not location:
                break

            nxt = urljoin(current, location)

            if not facebook_url(nxt):
                return jsonify(error="La redirection quitte Facebook."), 400

            if is_login_url(nxt):
                return jsonify(
                    final_url=nxt,
                    blocked=True,
                    original_url=original_url
                )

            current = nxt

        cleaned = clean_facebook_url(current)

        if urlparse(cleaned).path.lower().startswith("/people/"):
            fb_id = try_people_numeric(cleaned)
            if fb_id:
                cleaned = "https://www.facebook.com/profile.php?id=" + fb_id

        return jsonify(final_url=cleaned, blocked=False)

    except requests.RequestException:
        return jsonify(
            error="Facebook bloque actuellement la conversion automatique.",
            blocked=True,
            original_url=original_url
        ), 502
