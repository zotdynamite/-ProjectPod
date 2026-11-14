"""SQLite-shaped queries on Postgres.

Callers keep writing `?` placeholders and the SQLite date helpers already used
by the app. When DATABASE_URL is set, this module opens Postgres, requires
SSL, and rewrites those queries. The connection string is never logged.
"""
import os, re, urllib.parse

_SSL_STRONG = {"require", "verify-ca", "verify-full"}
_INSERT_OR_REPLACE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*$",
    re.IGNORECASE)
_DT_OFFSET = re.compile(r"datetime\(\s*'now'\s*,\s*'([+-]?\d+)\s+days'\s*\)", re.IGNORECASE)
_DATE_OFFSET = re.compile(r"date\(\s*'now'\s*,\s*'([+-]?\d+)\s+days'\s*\)", re.IGNORECASE)
_DATE_NOW = re.compile(r"date\(\s*'now'\s*\)", re.IGNORECASE)
_STRFTIME_CLOCK = re.compile(r"strftime\(\s*'%M:%S'\s*,\s*([A-Za-z_][\w.]*)\s*\)", re.IGNORECASE)
_DATE_COL = re.compile(r"date\(\s*([A-Za-z_][\w.]*)\s*\)", re.IGNORECASE)
_INSERT_HEAD = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)\b", re.IGNORECASE)
_ID_TABLES = {"shows", "boosts", "page_views"}


def postgres_enabled():
    return bool((os.environ.get("DATABASE_URL") or "").strip())


def require_ssl(url):
    """Return a DSN that negotiates SSL. verify-full and verify-ca are left alone."""
    url = (url or "").strip()
    if not url:
        return url
    if "://" in url:
        parts = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        current = next((value for key, value in pairs if key.lower() == "sslmode"), "")
        if current.lower() in _SSL_STRONG:
            return url
        pairs = [(key, value) for key, value in pairs if key.lower() != "sslmode"]
        pairs.append(("sslmode", "require"))
        query = urllib.parse.urlencode(pairs)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    kept = []
    current = ""
    for bit in url.split():
        if bit.lower().startswith("sslmode="):
            current = bit.split("=", 1)[1]
        else:
            kept.append(bit)
    if current.lower() in _SSL_STRONG:
        kept.append("sslmode=" + current)
    else:
        kept.append("sslmode=require")
    return " ".join(kept)


def redact(text):
    """Strip connection strings and passwords from a message before it is shown."""
    text = str(text or "")
    url = os.environ.get("DATABASE_URL") or ""
    if url:
        text = text.replace(url, "DATABASE_URL")
    text = re.sub(r"://[^\s/@]+@", "://***@", text)
    text = re.sub(r"(?i)\b(password|pwd)\s*=\s*\S+", r"\1=***", text)
    return text


def _stamp(match, pattern):
    offset = int(match.group(1))
    return (
        "to_char((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '"
        + str(offset) + " days', '" + pattern + "')"
    )


def translate(sql):
    """Turn one SQLite statement into SQL that psycopg can send to Postgres."""
    match = _INSERT_OR_REPLACE.match(sql.strip())
    if match:
        table, cols, vals = match.group(1), match.group(2), match.group(3)
        names = [part.strip() for part in cols.split(",")]
        updates = ", ".join(name + " = EXCLUDED." + name for name in names)
        sql = (
            f"INSERT INTO {table} ({cols}) VALUES ({vals}) "
            f"ON CONFLICT ({names[0]}) DO UPDATE SET {updates}"
        )
    sql = _DT_OFFSET.sub(lambda m: _stamp(m, "YYYY-MM-DD HH24:MI:SS"), sql)
    sql = _DATE_OFFSET.sub(lambda m: _stamp(m, "YYYY-MM-DD"), sql)
    sql = _DATE_NOW.sub(
        "to_char((CURRENT_TIMESTAMP AT TIME ZONE 'UTC'), 'YYYY-MM-DD')", sql)
    sql = _STRFTIME_CLOCK.sub(r"substring(\1 from 15 for 5)", sql)
    sql = _DATE_COL.sub(r"left(\1, 10)", sql)
    # psycopg treats % as a placeholder. Escape LIKE percents, then map ?.
    return sql.replace("%", "%%").replace("?", "%s")


def _with_returning(sql):
    if re.search(r"\bRETURNING\b", sql, re.IGNORECASE):
        return sql, False
    match = _INSERT_HEAD.match(sql)
    if not match or match.group(1).lower() not in _ID_TABLES:
        return sql, False
    return sql.rstrip().rstrip(";") + " RETURNING id", True


class Row:
    """Positional like a sqlite3.Row, and dict() works when names are unique."""

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        for name, value in zip(self._keys, self._values):
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return iter(self._values)

    def keys(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._values)


class Cursor:
    def __init__(self, rows, rowcount, lastrowid):
        self._rows = rows
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self, conn):
        self._conn = conn
        self._closed = False

    def execute(self, sql, params=()):
        translated, returning = _with_returning(translate(sql))
        if params is None:
            params = ()
        cur = self._conn.execute(translated, params)
        lastrowid = None
        rows = []
        if returning:
            got = cur.fetchone()
            if got is not None:
                lastrowid = got[0]
        elif cur.description is not None:
            keys = [col.name for col in cur.description]
            rows = [Row(keys, tuple(item)) for item in cur.fetchall()]
        return Cursor(rows, cur.rowcount, lastrowid)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def connect():
    """Open one Postgres connection. Import psycopg only when a URL is set."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("DATABASE_URL is set but psycopg is not installed.") from exc
    try:
        conn = psycopg.connect(require_ssl(url), connect_timeout=10)
    except Exception as exc:
        raise RuntimeError("Could not connect to Postgres: " + redact(exc)) from None
    # Neon’s pooled (PgBouncer) string is transaction-mode. Server-side
    # prepared statements do not survive the next checkout.
    conn.prepare_threshold = None
    return PostgresConnection(conn)
