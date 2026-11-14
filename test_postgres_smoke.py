"""Postgres seed, dialect queries, and survival across a process restart."""
import http.client, json, os, socket, subprocess, sys, time, unittest, urllib.parse

import app
import db as storage

ROOT = os.path.dirname(os.path.abspath(__file__))
HUMAN = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def using_postgres():
    return bool((os.environ.get("DATABASE_URL") or "").strip())


def drop_tables():
    with app.db() as conn:
        for table in ("page_views", "boosts", "shows", "meta"):
            conn.execute("DROP TABLE IF EXISTS " + table)


def one(sql, params=()):
    with app.db() as conn:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else tuple(row)


def all_rows(sql, params=()):
    with app.db() as conn:
        return [tuple(row) for row in conn.execute(sql, params)]


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start_server(port):
    env = os.environ.copy()
    env["PORT"] = str(port)
    env.pop("PODCAST_DB", None)
    return subprocess.Popen(
        [sys.executable, "-c",
         "import app\n"
         "app.init()\n"
         "app.ThreadingHTTPServer(('127.0.0.1', int(__import__('os').environ['PORT'])), app.H).serve_forever()\n"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_shows(proc, port):
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(storage.redact(proc.stdout.read() or "server exited"))
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/api/shows", headers={"User-Agent": "python-requests/test"})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            if resp.status == 200:
                return json.loads(body)
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server did not start")


def stop_server(proc):
    if proc.poll() is None:
        proc.terminate()
    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=5)
    return storage.redact(out or "")


def request(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, payload, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        conn.close()


@unittest.skipUnless(using_postgres(), "DATABASE_URL is not set")
class PostgresSmokeTests(unittest.TestCase):
    def setUp(self):
        drop_tables()

    def test_seed_is_idempotent_and_sqlite_queries_run(self):
        app.init()
        app.init()
        self.assertEqual(one("SELECT COUNT(*) FROM shows")[0], 32)
        self.assertEqual(one("SELECT COUNT(*) FROM shows WHERE name=?", ("Houston We Have a Podcast",))[0], 1)
        self.assertEqual(one("SELECT COUNT(*) FROM shows WHERE name=?", ("Python Bytes",))[0], 1)
        houston = one(
            """SELECT s.popularity, s.latest_title,
                      (SELECT COUNT(*) FROM boosts b WHERE b.show_id=s.id)
               FROM shows s WHERE s.name=?""",
            ("Houston We Have a Podcast",))
        self.assertEqual(houston, (2, "The Gunslingers of White Sands", 1))
        python_bytes = one(
            """SELECT s.popularity, (SELECT COUNT(*) FROM boosts b WHERE b.show_id=s.id)
               FROM shows s WHERE s.name=?""",
            ("Python Bytes",))
        self.assertEqual(python_bytes, (2, 1))
        self.assertEqual(one("SELECT value FROM meta WHERE key=?", ("catalog",))[0], app.CATALOG_KEY)
        self.assertEqual(
            one("SELECT value FROM meta WHERE key=?", ("listener_seed",))[0],
            "houston-we-have-a-podcast+python-bytes")
        created = one(
            "SELECT b.created FROM boosts b JOIN shows s ON s.id=b.show_id WHERE s.name=?",
            ("Python Bytes",))[0]
        self.assertEqual(created, "2026-11-07 16:52:02")

        with app.db() as conn:
            cur = conn.execute(
                """INSERT INTO shows(name,rss,category,description,creator,demo)
                   VALUES(?,?,?,?,?,1)""",
                ("Midnight Ledger", "https://example.com/midnight.xml", "Arts", "Demo", "Nobody"))
            demo_id = cur.lastrowid
            for _ in range(3):
                conn.execute(
                    "INSERT INTO boosts(show_id, created) VALUES(?,?)",
                    (demo_id, "2026-01-01 00:12:34"))
            conn.execute(
                """INSERT INTO shows(name,rss,category,description,creator,demo)
                   VALUES(?,?,?,?,?,0)""",
                ("Example Feed", "https://example.com/other.xml", "Arts", "Demo", "Nobody"))
        app.init()
        self.assertEqual(
            all_rows("SELECT name FROM shows WHERE name IN (?,?)", ("Midnight Ledger", "Example Feed")),
            [])
        self.assertEqual(one("SELECT COUNT(*) FROM shows")[0], 32)
        fresh = one(
            """SELECT created FROM boosts WHERE show_id=(SELECT id FROM shows WHERE name=?)
               ORDER BY id DESC LIMIT 1""",
            ("Houston We Have a Podcast",))
        # The copied boost is older than a boost written with the column default.
        with app.db() as conn:
            conn.execute(
                "INSERT INTO boosts(show_id) SELECT id FROM shows WHERE name=?",
                ("Houston We Have a Podcast",))
        stamped = one(
            """SELECT created FROM boosts WHERE show_id=(SELECT id FROM shows WHERE name=?)
               ORDER BY id DESC LIMIT 1""",
            ("Houston We Have a Podcast",))[0]
        self.assertRegex(stamped, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotEqual(stamped, fresh[0] if fresh else None)
        sid = one("SELECT id FROM shows WHERE name=?", ("Houston We Have a Podcast",))[0]
        self.assertTrue(any(show_id == sid for show_id in app.weekly_map()))
        self.assertTrue(app.boost_series(sid))
        self.assertTrue(app.chart_rows(view="rising"))
        self.assertEqual(len(app.chart_rows(view="new")), 32)
        self.assertEqual(app.visit_stats()["all_time"]["views"], 0)

    def test_boosts_submissions_and_page_views_survive_restart(self):
        port = free_port()
        proc = start_server(port)
        try:
            shows = wait_shows(proc, port)
        except Exception:
            stop_server(proc)
            raise
        try:
            self.assertEqual(len(shows), 32)
            houston = next(show for show in shows if show["name"] == "Houston We Have a Podcast")
            status, _, headers = request(
                port, "POST", f"/show/{houston['id']}/boost",
                {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": HUMAN}, b"")
            self.assertEqual(status, 303)
            self.assertTrue(headers.get("location", "").endswith(f"/show/{houston['id']}"))
            body = urllib.parse.urlencode({
                "name": "Persistence Check Show",
                "rss": "http://127.0.0.1:1/feed.xml",
                "category": "Technology",
                "description": "Stored in Postgres across restarts.",
                "creator": "Test Listener",
                "cover": "",
                "website": "",
            }).encode()
            status, _, headers = request(
                port, "POST", "/submit",
                {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": HUMAN,
                 "Content-Length": str(len(body))}, body)
            self.assertEqual(status, 303, headers)
            self.assertIn("/show/", headers.get("location", ""))
            status, page, _ = request(port, "GET", "/", {"User-Agent": HUMAN, "X-Forwarded-For": "203.0.113.50"})
            self.assertEqual(status, 200)
            self.assertIn(b"Podcast Stream", page)
        finally:
            log = stop_server(proc)
        self.assertNotIn("203.0.113.50", log)
        self.assertNotIn("postgresql://", log.lower())

        def snapshot():
            return {
                "shows": one("SELECT COUNT(*) FROM shows")[0],
                "houston": one(
                    """SELECT popularity, (SELECT COUNT(*) FROM boosts b WHERE b.show_id=s.id)
                       FROM shows s WHERE name=?""",
                    ("Houston We Have a Podcast",)),
                "python": one(
                    """SELECT popularity, (SELECT COUNT(*) FROM boosts b WHERE b.show_id=s.id)
                       FROM shows s WHERE name=?""",
                    ("Python Bytes",)),
                "submission": one(
                    "SELECT popularity, creator, category FROM shows WHERE name=?",
                    ("Persistence Check Show",)),
                "views": all_rows("SELECT path FROM page_views ORDER BY id"),
            }

        after_stop = snapshot()
        self.assertEqual(after_stop["shows"], 33)
        self.assertEqual(after_stop["houston"], (3, 2))
        self.assertEqual(after_stop["python"], (2, 1))
        self.assertEqual(after_stop["submission"], (1, "Test Listener", "Technology"))
        self.assertEqual(after_stop["views"], [("/",)])
        self.assertNotIn("203.0.113.50", json.dumps(after_stop))

        port = free_port()
        proc = start_server(port)
        try:
            shows = wait_shows(proc, port)
            self.assertEqual(len(shows), 33)
            self.assertEqual(sum(1 for show in shows if show["name"] == "Houston We Have a Podcast"), 1)
        finally:
            stop_server(proc)
        self.assertEqual(snapshot(), after_stop)


if __name__ == "__main__":
    unittest.main()
