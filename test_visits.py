#!/usr/bin/env python3
"""Visitor counter: hash privacy, bot skips, stats auth, and aggregate counts."""
import base64, http.client, io, json, os, sqlite3, tempfile, threading, unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone

import app

HUMAN = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TOKEN = "stats-token-7f3a9c2e"
BOTS = (
    "Twitterbot/1.0",
    "facebookexternalhit/1.1",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Slackbot-LinkExpanding 1.0",
    "Discordbot/2.0",
    "LinkedInBot/1.0",
    "curl/8.5.0",
    "python-requests/2.32.3",
    "Mozilla/5.0 (compatible; SomeCrawler/1.0)",
    "SomeSpider/1.0",
    "LinkPreview/1.0",
    "GenericBot/1.0",
)


class VisitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_db = app.DB
        app.DB = os.path.join(self.tmp.name, "visits.db")
        app._daily_salt["day"] = ""
        app._daily_salt["value"] = ""
        self._old_token = os.environ.pop("STATS_TOKEN", None)
        app.init()
        self.httpd = app.ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        app.DB = self._old_db
        app._daily_salt["day"] = ""
        app._daily_salt["value"] = ""
        if self._old_token is None:
            os.environ.pop("STATS_TOKEN", None)
        else:
            os.environ["STATS_TOKEN"] = self._old_token
        self.tmp.cleanup()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload, {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()

    def rows(self):
        conn = sqlite3.connect(app.DB)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(
                "SELECT path, viewed_at, referrer, visitor_hash FROM page_views ORDER BY id")]
        finally:
            conn.close()

    def db_bytes(self):
        with open(app.DB, "rb") as handle:
            return handle.read()

    def test_salt_rotates_and_hash_hides_the_ip(self):
        first = app.daily_salt("2026-01-01")
        self.assertEqual(app.daily_salt("2026-01-01"), first)
        second = app.daily_salt("2026-01-02")
        self.assertNotEqual(first, second)
        self.assertEqual(app._daily_salt, {"day": "2026-01-02", "value": second})
        ip, ua = "203.0.113.40", "UA-only-for-hash"
        hashed = app.visitor_hash(first, ip, ua)
        self.assertEqual(len(hashed), 64)
        self.assertNotEqual(hashed, app.visitor_hash(second, ip, ua))
        self.assertNotIn(ip, hashed)
        self.assertNotIn(ua, hashed)
        self.assertNotIn(".", hashed)
        raw = self.db_bytes()
        self.assertNotIn(first.encode(), raw)
        self.assertNotIn(second.encode(), raw)
        self.assertNotIn(ip.encode(), raw)
        cols = sqlite3.connect(app.DB).execute("PRAGMA table_info(page_views)").fetchall()
        self.assertEqual([col[1] for col in cols], ["id", "path", "viewed_at", "referrer", "visitor_hash"])
        self.assertEqual(app.clean_referrer("https://news.example/story?utm=1#frag"), "https://news.example/story")
        self.assertEqual(app.clean_referrer("https://user:secret@news.example:8443/a?x=1"), "https://news.example:8443/a")
        self.assertEqual(app.clean_referrer("https://[2001:db8::5]/a?b=1"), "https://[2001:db8::5]/a")
        self.assertEqual(app.clean_referrer("javascript:alert(1)"), "")
        self.assertEqual(app.client_ip({"X-Forwarded-For": " 198.51.100.4, 10.0.0.1"}, "127.0.0.1"), "198.51.100.4")
        self.assertEqual(app.client_ip({"X-Forwarded-For": ", 10.0.0.1"}, "192.0.2.5"), "192.0.2.5")
        self.assertEqual(app.client_ip({}, "127.0.0.1"), "127.0.0.1")
        self.assertEqual(app.request_path("/?q=secret-search"), "/")
        self.assertFalse(app.is_noise("GET", {"User-Agent": HUMAN}))
        self.assertTrue(app.is_noise("HEAD", {"User-Agent": HUMAN}))
        self.assertTrue(app.is_noise("OPTIONS", {"User-Agent": HUMAN}))
        self.assertTrue(app.is_noise("GET", {"User-Agent": HUMAN, "Render-Health-Check": "1"}))
        self.assertTrue(app.is_noise("GET", {"User-Agent": "Render/1.0"}))
        for ua_bot in BOTS:
            self.assertTrue(app.is_noise("GET", {"User-Agent": ua_bot}), ua_bot)

    def test_page_views_skip_noise_and_keep_raw_ip_out_of_the_database(self):
        ip1, ip2 = "203.0.113.10", "203.0.113.20"
        secret_query = "utm=supersecret-referrer"
        human = {
            "User-Agent": HUMAN,
            "X-Forwarded-For": f"{ip1}, 10.0.0.8",
            "Referer": f"https://user:letmein@news.example/story?{secret_query}",
        }
        status, body, _ = self.request("GET", "/?q=secret-search", human)
        self.assertEqual(status, 200)
        self.assertIn(b"Podcast Stream", body)
        self.assertNotIn(ip1.encode(), body)
        self.request("GET", "/", human)
        self.request("GET", "/charts", {
            "User-Agent": HUMAN,
            "X-Forwarded-For": ip2,
            "Referer": "https://reddit.com/r/podcasts?x=1",
        })
        before = len(self.rows())
        for ua in BOTS:
            status, _, _ = self.request("GET", "/", {"User-Agent": ua, "X-Forwarded-For": "203.0.113.99"})
            self.assertEqual(status, 200)
        self.request("GET", "/", {"User-Agent": HUMAN, "Render-Health-Check": "1", "X-Forwarded-For": "203.0.113.99"})
        self.request("GET", "/", {"User-Agent": "Render/1.0", "X-Forwarded-For": "203.0.113.99"})
        self.request("HEAD", "/", {"User-Agent": HUMAN, "X-Forwarded-For": ip1})
        self.request("OPTIONS", "/", {"User-Agent": HUMAN, "X-Forwarded-For": ip1})
        css, _, _ = self.request("GET", "/static/style.css", {"User-Agent": HUMAN, "X-Forwarded-For": ip1})
        api, api_body, _ = self.request("GET", "/api/shows", {"User-Agent": HUMAN, "X-Forwarded-For": ip1})
        self.assertEqual(css, 200)
        self.assertEqual(api, 200)
        self.assertTrue(api_body.startswith(b"["))
        self.assertEqual(len(self.rows()), before)
        self.assertEqual(before, 3)

        stored = self.rows()
        self.assertEqual([row["path"] for row in stored], ["/", "/", "/charts"])
        self.assertEqual(stored[0]["referrer"], "https://news.example/story")
        self.assertEqual(stored[2]["referrer"], "https://reddit.com/r/podcasts")
        salt = app.daily_salt()
        self.assertEqual(stored[0]["visitor_hash"], app.visitor_hash(salt, ip1, HUMAN))
        self.assertEqual(stored[1]["visitor_hash"], stored[0]["visitor_hash"])
        self.assertEqual(stored[2]["visitor_hash"], app.visitor_hash(salt, ip2, HUMAN))
        self.assertNotEqual(stored[0]["visitor_hash"], app.visitor_hash(salt, "10.0.0.8", HUMAN))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertTrue(stored[0]["viewed_at"].startswith(today))
        blob = json.dumps(stored)
        for secret in (ip1, ip2, "10.0.0.8", "203.0.113.99", "letmein", secret_query, "secret-search", HUMAN):
            self.assertNotIn(secret, blob)
        raw = self.db_bytes()
        for secret in (ip1, ip2, "10.0.0.8", "203.0.113.99", "letmein", secret_query, "secret-search", HUMAN):
            self.assertNotIn(secret.encode(), raw)

        # No forwarded header: hash the socket address, still without storing it.
        self.request("GET", "/browse", {"User-Agent": HUMAN})
        direct = self.rows()[-1]
        self.assertEqual(direct["path"], "/browse")
        self.assertEqual(direct["visitor_hash"], app.visitor_hash(salt, "127.0.0.1", HUMAN))
        self.assertNotIn("127.0.0.1", json.dumps(direct))

    def test_stats_auth_and_aggregate_counts(self):
        ip = "203.0.113.10"
        human = {
            "User-Agent": HUMAN,
            "X-Forwarded-For": ip,
            "Referer": "https://user:letmein@news.example/story?utm=supersecret-referrer",
        }
        self.request("GET", "/", human)
        self.request("GET", "/", human)
        self.request("GET", "/charts", {
            "User-Agent": HUMAN,
            "X-Forwarded-For": "203.0.113.20",
            "Referer": "https://reddit.com/r/podcasts?x=1",
        })
        now = datetime.now(timezone.utc)
        two_days = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        eight_days = (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(app.DB)
        conn.execute("INSERT INTO page_views(path, viewed_at, referrer, visitor_hash) VALUES(?,?,?,?)",
                     ("/about", two_days, "https://old.example/post", "a" * 64))
        conn.execute("INSERT INTO page_views(path, viewed_at, referrer, visitor_hash) VALUES(?,?,?,?)",
                     ("/browse", now.strftime("%Y-%m-%d %H:%M:%S"), "", "c" * 64))
        conn.execute("INSERT INTO page_views(path, viewed_at, referrer, visitor_hash) VALUES(?,?,?,?)",
                     ("/submit", eight_days, "https://ancient.example/", "b" * 64))
        conn.commit()
        conn.close()

        def bearer(token):
            return {"Authorization": f"Bearer {token}", "User-Agent": HUMAN}

        def basic(user, password):
            raw = base64.b64encode(f"{user}:{password}".encode()).decode()
            return {"Authorization": f"Basic {raw}", "User-Agent": HUMAN}

        os.environ["STATS_TOKEN"] = TOKEN
        status, body, hdrs = self.request("GET", "/api/stats", {"User-Agent": HUMAN})
        self.assertEqual(status, 401)
        self.assertNotIn(b"top_pages", body)
        self.assertIn("basic", hdrs.get("www-authenticate", "").lower())
        status, body, _ = self.request("GET", "/stats", bearer("wrong-token"))
        self.assertEqual(status, 401)
        self.assertNotIn(b"Top pages", body)
        status, _, _ = self.request("GET", "/stats", basic("stats", "nope"))
        self.assertEqual(status, 401)
        status, _, _ = self.request("GET", "/stats", basic(TOKEN, ""))
        self.assertEqual(status, 401)

        buf = io.StringIO()
        with redirect_stderr(buf):
            status, body, _ = self.request(
                "GET", f"/stats?token={TOKEN}&x=1",
                {"User-Agent": HUMAN, "X-Forwarded-For": "203.0.113.77"})
        self.assertEqual(status, 401)
        self.assertNotIn(b"Top pages", body)
        logged = buf.getvalue()
        self.assertNotIn(TOKEN, logged)
        self.assertNotIn("203.0.113.77", logged)
        self.assertNotIn("?", logged)
        raw = self.db_bytes()
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertNotIn(b"203.0.113.77", raw)

        status, body, hdrs = self.request("GET", "/api/stats", {"Authorization": f"bearer {TOKEN}", "User-Agent": HUMAN})
        self.assertEqual(status, 200)
        self.assertTrue(hdrs.get("content-type", "").startswith("application/json"))
        self.assertEqual(hdrs.get("cache-control"), "no-store")
        self.assertNotIn(TOKEN.encode(), body)
        report = json.loads(body)
        self.assertEqual(report["today"], {"views": 4, "visitors": 3})
        self.assertEqual(report["last_7_days"], {"views": 5, "visitors": 4})
        self.assertEqual(report["all_time"], {"views": 6, "visitors": 5})
        self.assertEqual(report["top_pages"], [
            {"path": "/", "views": 2, "visitors": 1},
            {"path": "/about", "views": 1, "visitors": 1},
            {"path": "/browse", "views": 1, "visitors": 1},
            {"path": "/charts", "views": 1, "visitors": 1},
        ])
        self.assertEqual(report["top_referrers"], [
            {"referrer": "https://news.example/story", "views": 2, "visitors": 1},
            {"referrer": "https://old.example/post", "views": 1, "visitors": 1},
            {"referrer": "https://reddit.com/r/podcasts", "views": 1, "visitors": 1},
        ])
        self.assertNotIn(app.daily_salt(), json.dumps(report))

        status, body, _ = self.request("GET", "/stats", basic("owner", TOKEN))
        self.assertEqual(status, 200)
        self.assertIn(b"Today (UTC)", body)
        self.assertIn(b"Top pages", body)
        self.assertIn(b"Top referrers", body)
        self.assertIn(b"https://news.example/story", body)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertNotIn(b"203.0.113.10", body)
        self.assertNotIn(b"letmein", body)

        os.environ.pop("STATS_TOKEN", None)
        status, body, _ = self.request("GET", "/stats", {"User-Agent": HUMAN})
        self.assertEqual(status, 404)
        self.assertNotIn(b"Top pages", body)
        status, body, _ = self.request("GET", "/api/stats", bearer(TOKEN))
        self.assertEqual(status, 404)
        self.assertNotIn(b"top_pages", body)
        os.environ["STATS_TOKEN"] = "   "
        status, body, _ = self.request("GET", "/api/stats", bearer(TOKEN))
        self.assertEqual(status, 404)
        self.assertNotIn(b"top_pages", body)

    def test_logging_failure_and_posts_do_not_break_or_count(self):
        original = app.daily_salt
        app.daily_salt = lambda today=None: (_ for _ in ()).throw(RuntimeError("salt unavailable"))
        try:
            status, body, _ = self.request("GET", "/", {"User-Agent": HUMAN, "X-Forwarded-For": "203.0.113.55"})
        finally:
            app.daily_salt = original
        self.assertEqual(status, 200)
        self.assertIn(b"Discover podcasts", body)
        self.assertEqual(self.rows(), [])
        self.assertNotIn(b"203.0.113.55", self.db_bytes())

        conn = sqlite3.connect(app.DB)
        sid, before = conn.execute("SELECT id, popularity FROM shows ORDER BY id LIMIT 1").fetchone()
        conn.close()
        status, _, hdrs = self.request(
            "POST", f"/show/{sid}/boost",
            {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": HUMAN,
             "X-Forwarded-For": "203.0.113.55"},
            body=b"")
        self.assertEqual(status, 303)
        self.assertTrue(hdrs.get("location", "").endswith(f"/show/{sid}"))
        self.assertEqual(self.rows(), [])
        conn = sqlite3.connect(app.DB)
        after = conn.execute("SELECT popularity FROM shows WHERE id=?", (sid,)).fetchone()[0]
        boosts = conn.execute("SELECT COUNT(*) FROM boosts WHERE show_id=?", (sid,)).fetchone()[0]
        conn.close()
        self.assertEqual(after, before + 1)
        self.assertEqual(boosts, 1)
        self.assertNotIn(b"203.0.113.55", self.db_bytes())


if __name__ == "__main__":
    unittest.main()
