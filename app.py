from flask import Flask, request, jsonify, send_from_directory
import requests
import re
import html
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin, parse_qs, urlencode

import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
})

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
CACHE_DAYS = int(os.environ.get("CACHE_DAYS", "30"))


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

        return (
            path == "/login"
            or path.startswith("/login/")
        )

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
                return (
                    "https://www.facebook.com/profile.php?"
                    + urlencode({"id": fb_id})
                )

        keep = {}

        for key in ("id", "story_fbid", "fbid", "v"):

            if key in qs and qs[key]:
                keep[key] = qs[key][0]

        base = "https://www.facebook.com" + path

        return (
            base
            + ("?" + urlencode(keep) if keep else "")
        )

    except Exception:
        return url


def db_connect():

    if not DATABASE_URL:
        return None

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init_db():

    if not DATABASE_URL:
        return

    try:

        with db_connect() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS facebook_link_cache (
                        original_url TEXT PRIMARY KEY,
                        final_url TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        hit_count BIGINT NOT NULL DEFAULT 0
                    )
                """)

                cur.execute("""
                    CREATE INDEX IF NOT EXISTS
                    idx_facebook_link_cache_created_at
                    ON facebook_link_cache(created_at)
                """)

            conn.commit()

    except Exception as e:
        print("Cache DB init error:", e)


def cache_get(original_url):

    if not DATABASE_URL:
        return None

    try:

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=CACHE_DAYS)
        )

        with db_connect() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT final_url
                    FROM facebook_link_cache
                    WHERE original_url = %s
                    AND created_at >= %s
                """, (original_url, cutoff))

                row = cur.fetchone()

                if not row:
                    return None

                cur.execute("""
                    UPDATE facebook_link_cache
                    SET
                        last_used_at = NOW(),
                        hit_count = hit_count + 1
                    WHERE original_url = %s
                """, (original_url,))

            conn.commit()

        return row["final_url"]

    except Exception as e:

        print("Cache read error:", e)

        return None


def cache_put(original_url, final_url):

    if not DATABASE_URL:
        return

    if not final_url or is_login_url(final_url):
        return

    try:

        with db_connect() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO facebook_link_cache
                    (
                        original_url,
                        final_url,
                        created_at,
                        last_used_at,
                        hit_count
                    )

                    VALUES (%s, %s, NOW(), NOW(), 0)

                    ON CONFLICT (original_url)

                    DO UPDATE SET
                        final_url = EXCLUDED.final_url,
                        created_at = NOW(),
                        last_used_at = NOW()
                """, (
                    original_url,
                    final_url
                ))

            conn.commit()

    except Exception as e:

        print("Cache write error:", e)


def try_people_numeric(url):

    try:

        if not urlparse(url).path.lower().startswith("/people/"):
            return None

        r = session.get(
            url,
            timeout=8,
            allow_redirects=True
        )

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

            m = re.search(
                pat,
                text,
                re.I
            )

            if m:
                return m.group(1)

    except requests.RequestException:
        pass

    return None


@app.get("/")
def index():

    return send_from_directory(
        ".",
        "index.html"
    )


@app.get("/api/cache-status")
def cache_status():

    if not DATABASE_URL:

        return jsonify(
            enabled=False,
            connected=False
        )

    try:

        with db_connect() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT COUNT(*) AS n
                    FROM facebook_link_cache
                """)

                row = cur.fetchone()

        return jsonify(
            enabled=True,
            connected=True,
            entries=row["n"],
            cache_days=CACHE_DAYS
        )

    except Exception:

        return jsonify(
            enabled=True,
            connected=False
        ), 503


@app.post("/api/resolve")
def resolve():

    data = request.get_json(
        silent=True
    ) or {}

    original_url = data.get(
        "url",
        ""
    ).strip()

    if not facebook_url(original_url):

        return jsonify(
            error="Colle un lien Facebook valide."
        ), 400


    # CACHE AVANT FACEBOOK

    cached = cache_get(original_url)

    if cached:

        return jsonify(
            final_url=cached,
            blocked=False,
            cached=True
        )


    try:

        current = original_url


        # MAXIMUM 5 REDIRECTIONS

        for _ in range(5):

            r = session.get(
                current,
                allow_redirects=False,
                timeout=8
            )

            if not (
                r.is_redirect
                or r.is_permanent_redirect
            ):
                break


            location = r.headers.get(
                "Location"
            )

            if not location:
                break


            nxt = urljoin(
                current,
                location
            )


            if not facebook_url(nxt):

                return jsonify(
                    error="La redirection quitte Facebook."
                ), 400


            # FACEBOOK DEMANDE LOGIN

            if is_login_url(nxt):

                return jsonify(
                    final_url=nxt,
                    blocked=True,
                    cached=False,
                    original_url=original_url
                )


            current = nxt


        cleaned = clean_facebook_url(
            current
        )


        if is_login_url(cleaned):

            return jsonify(
                final_url=cleaned,
                blocked=True,
                cached=False,
                original_url=original_url
            )


        # LIENS /PEOPLE/

        if urlparse(
            cleaned
        ).path.lower().startswith("/people/"):

            fb_id = try_people_numeric(
                cleaned
            )

            if fb_id:

                cleaned = (
                    "https://www.facebook.com/profile.php?id="
                    + fb_id
                )


        # ENREGISTREMENT CACHE

        cache_put(
            original_url,
            cleaned
        )


        return jsonify(
            final_url=cleaned,
            blocked=False,
            cached=False
        )


    except requests.RequestException:

        return jsonify(
            error="Facebook bloque actuellement la conversion automatique.",
            blocked=True,
            cached=False,
            original_url=original_url
        ), 502


init_db()
