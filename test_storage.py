"""Query translation and SQLite startup when psycopg is not importable."""
import builtins, os, tempfile, unittest

import app
import db as storage


class TranslateTests(unittest.TestCase):
    def test_placeholders_and_like_wildcards(self):
        sql = storage.translate(
            "SELECT id FROM shows WHERE name=? OR lower(rss) LIKE 'https://example.com/%' "
            "OR lower(rss) LIKE 'http://example.com/%'")
        self.assertEqual(
            sql,
            "SELECT id FROM shows WHERE name=%s OR lower(rss) LIKE 'https://example.com/%%' "
            "OR lower(rss) LIKE 'http://example.com/%%'")

    def test_dates_clocks_and_upsert(self):
        upsert = storage.translate("INSERT OR REPLACE INTO meta(key,value) VALUES('catalog',?)")
        self.assertIn("ON CONFLICT (key) DO UPDATE SET key = EXCLUDED.key, value = EXCLUDED.value", upsert)
        self.assertIn("%s", upsert)
        self.assertNotIn("?", upsert)
        self.assertNotIn("OR REPLACE", upsert.upper())
        week = storage.translate(
            "SELECT date(created) AS day FROM boosts WHERE created >= datetime('now','-7 days') "
            "AND created >= datetime('now', '-7 days')")
        self.assertIn("left(created, 10) AS day", week)
        self.assertEqual(week.count("INTERVAL '-7 days'"), 2)
        self.assertNotIn("datetime(", week)
        self.assertNotIn("?", week)
        self.assertEqual(
            storage.translate("SELECT date('now','-6 days'), date('now')"),
            "SELECT to_char((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '-6 days', 'YYYY-MM-DD'), "
            "to_char((CURRENT_TIMESTAMP AT TIME ZONE 'UTC'), 'YYYY-MM-DD')")
        self.assertEqual(
            storage.translate("SELECT strftime('%M:%S', b.created)"),
            "SELECT substring(b.created from 15 for 5)")

    def test_require_ssl(self):
        plain = storage.require_ssl("postgresql://podcast:secret@db.example/podcast")
        self.assertIn("sslmode=require", plain)
        self.assertIn("podcast:secret@", plain)
        kept = storage.require_ssl("postgresql://podcast:secret@db.example/podcast?sslmode=verify-full&channel_binding=require")
        self.assertIn("sslmode=verify-full", kept)
        self.assertNotIn("sslmode=require", kept)
        self.assertIn("channel_binding=require", kept)
        weaker = storage.require_ssl("postgresql://podcast:secret@db.example/podcast?sslmode=prefer")
        self.assertIn("sslmode=require", weaker)
        self.assertNotIn("sslmode=prefer", weaker)
        keywords = storage.require_ssl("host=localhost dbname=podcast password=secret sslmode=disable")
        self.assertIn("sslmode=require", keywords)
        self.assertNotIn("sslmode=disable", keywords)
        self.assertIn("password=secret", keywords)

    def test_redact_hides_the_connection_string(self):
        old = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://podcast:secret-pass@ep.example/db"
        try:
            text = storage.redact(
                "failed postgresql://podcast:secret-pass@ep.example/db password=secret-pass")
        finally:
            if old is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old
        self.assertNotIn("secret-pass", text)
        self.assertNotIn("postgresql://podcast", text)

    def test_sqlite_starts_when_psycopg_cannot_be_imported(self):
        real_import = builtins.__import__

        def blocked(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "psycopg" or name.startswith("psycopg."):
                raise ImportError("psycopg blocked")
            return real_import(name, globals, locals, fromlist, level)

        old_url = os.environ.pop("DATABASE_URL", None)
        old_db = app.DB
        tmp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(tmp.name, "plain.db")
        builtins.__import__ = blocked
        try:
            app.init()
            with app.db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM shows").fetchone()[0]
            self.assertGreaterEqual(count, 30)
            self.assertFalse(storage.postgres_enabled())
        finally:
            builtins.__import__ = real_import
            app.DB = old_db
            if old_url is not None:
                os.environ["DATABASE_URL"] = old_url
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
