#!/usr/bin/env python3
"""Podcast Stream v1 - zero-dependency podcast directory (Python stdlib only)."""
import base64, hashlib, hmac, html, json, os, re, secrets, sqlite3, sys, threading, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("PODCAST_DB") or os.path.join(BASE, "podcast_steam.db")
CATEGORIES = ["Arts","Business","Comedy","Education","Fiction","Government","Health & Fitness","History",
 "Kids & Family","Leisure","Music","News","Religion & Spirituality","Science","Society & Culture","Sports",
 "Technology","True Crime","TV & Film"]
with open(os.path.join(BASE, "catalog.json"), encoding="utf-8") as catalog_file:
    CATALOG_FILE = json.load(catalog_file)
CATALOG = CATALOG_FILE["shows"]
# Titles shipped as the fictional first-run seed, plus the example.com feeds those rows used.
DEMO_NAMES = (
    "Midnight Ledger", "Orbit Kitchen", "Pencil & Pixel", "The Quiet Verdict",
    "Laugh Track Lost", "Byte-Sized Mornings", "Little Lantern Stories",
    "Old Roads Radio", "Stride Club", "Reel Talkers", "Bassline Diaries", "Sideline Sundays",
)
# Invented boost rows the preview inserted when the boosts table was empty.
# Houston We Have a Podcast is a real submission that also received 7 of those rows.
PREVIEW_BOOSTS = {
    "Pencil & Pixel": 32, "Stride Club": 25, "Sideline Sundays": 19, "Orbit Kitchen": 12,
    "Houston We Have a Podcast": 7, "Laugh Track Lost": 6, "Midnight Ledger": 5,
    "Little Lantern Stories": 4, "Old Roads Radio": 3, "Reel Talkers": 3,
    "Bassline Diaries": 2, "The Quiet Verdict": 2, "Byte-Sized Mornings": 1,
}
CATALOG_KEY = "apple-us-top-2026-11-13"
PALETTES = [("#ff6b3d","#7b2ff7"),("#00c2a8","#1e3a8a"),("#f7b500","#d6246e"),("#3b82f6","#06b6d4"),
 ("#ef4444","#7c2d12"),("#10b981","#4338ca"),("#ec4899","#f59e0b"),("#8b5cf6","#0ea5e9")]

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS shows(id INTEGER PRIMARY KEY, name TEXT NOT NULL, rss TEXT NOT NULL,
          category TEXT NOT NULL, description TEXT NOT NULL, cover TEXT, creator TEXT NOT NULL, website TEXT,
          popularity INTEGER NOT NULL DEFAULT 0, promote_requested INTEGER DEFAULT 0, featured INTEGER DEFAULT 0,
          demo INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP)""")
        columns = {r[1] for r in c.execute("PRAGMA table_info(shows)")}
        for name, kind in (("latest_title", "TEXT"), ("latest_audio", "TEXT"), ("latest_published", "TEXT"), ("latest_duration", "TEXT"), ("latest_description", "TEXT"), ("episode_count", "INTEGER")):
            if name not in columns: c.execute(f"ALTER TABLE shows ADD COLUMN {name} {kind}")
        c.execute("""CREATE TABLE IF NOT EXISTS boosts (
            id INTEGER PRIMARY KEY, show_id INTEGER NOT NULL,
            created TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("CREATE INDEX IF NOT EXISTS boosts_recent ON boosts(created, show_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS page_views (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            viewed_at TEXT NOT NULL,
            referrer TEXT NOT NULL DEFAULT '',
            visitor_hash TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS page_views_recent ON page_views(viewed_at, path)")
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        remove_demo_shows(c)
        seed_catalog(c)

def remove_demo_shows(c):
    """Drop the fictional seed, wherever an older database file still has it."""
    marks = ",".join("?" * len(DEMO_NAMES))
    demo_ids = [row[0] for row in c.execute(
        f"""SELECT id FROM shows WHERE demo=1 OR name IN ({marks})
            OR lower(rss) LIKE 'https://example.com/%' OR lower(rss) LIKE 'http://example.com/%'""",
        DEMO_NAMES)]
    if not demo_ids:
        return
    id_marks = ",".join("?" * len(demo_ids))
    clocks = [row[0] for row in c.execute(
        f"SELECT strftime('%M:%S', created) FROM boosts WHERE show_id IN ({id_marks}) AND created IS NOT NULL",
        demo_ids)]
    if clocks:
        from collections import Counter
        clock, count = Counter(clocks).most_common(1)[0]
        # The preview wrote every invented boost at one clock time. Real boosts on a
        # kept show do not share that minute:second. Cap the delete at the seeded count
        # and remove the oldest rows so a later real boost is the one that stays.
        if count >= 3:
            for name, seeded in PREVIEW_BOOSTS.items():
                c.execute(
                    """DELETE FROM boosts WHERE id IN (
                        SELECT b.id FROM boosts b JOIN shows s ON s.id=b.show_id
                        WHERE s.name=? AND strftime('%M:%S', b.created)=?
                        ORDER BY b.id LIMIT ?)""",
                    (name, clock, seeded))
    c.execute(f"DELETE FROM boosts WHERE show_id IN ({id_marks})", demo_ids)
    c.execute(f"DELETE FROM shows WHERE id IN ({id_marks})", demo_ids)

def seed_catalog(c):
    """Insert the real catalog once. Listener-submitted shows are left in place."""
    done = c.execute("SELECT value FROM meta WHERE key='catalog'").fetchone()
    if done and done[0] == CATALOG_KEY:
        return
    have_rss = {row[0] for row in c.execute("SELECT rss FROM shows")}
    have_names = {row[0].casefold() for row in c.execute("SELECT name FROM shows")}
    ordered = sorted(CATALOG, key=lambda show: (show.get("chart_rank") is None, show.get("chart_rank") or 10**9, show["name"].casefold()))
    for show in ordered:
        if show["rss"] in have_rss or show["name"].casefold() in have_names:
            continue
        if show["category"] not in CATEGORIES:
            continue
        c.execute("""INSERT INTO shows(name,rss,category,description,cover,creator,website,popularity,promote_requested,featured,demo)
                     VALUES(?,?,?,?,?,?,?,0,0,0,0)""",
                  (show["name"], show["rss"], show["category"], show["description"], show["cover"], show["creator"], show["website"]))
        have_rss.add(show["rss"])
        have_names.add(show["name"].casefold())
    wanted = {show["rss"] for show in CATALOG} | {show["name"].casefold() for show in CATALOG}
    present = sum(1 for row in c.execute("SELECT name, rss FROM shows") if row[1] in wanted or row[0].casefold() in wanted)
    if present >= len(CATALOG):
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('catalog',?)", (CATALOG_KEY,))

def ranked(category=None):
    """Rank computed live from popularity (ties: older first). Promotion never affects rank."""
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM shows ORDER BY popularity DESC, id ASC")]
    for i,r in enumerate(rows,1): r["rank"] = i
    if category:
        rows = [r for r in rows if r["category"]==category]
        for i,r in enumerate(rows,1): r["cat_rank"] = i
    return rows

e = lambda s: html.escape(str(s or ""), quote=True)

def cover(s, size="", lazy=True):
    lazy_attr = ' loading="lazy"' if lazy else ""
    if s.get("cover"):
        return f'<img class="cover {size}" src="{e(s["cover"])}" alt="Cover art for {e(s["name"])}"{lazy_attr}>'
    a,b = PALETTES[s["id"] % len(PALETTES)]
    words = re.findall(r"[A-Za-z0-9]+", s["name"])
    ini = "".join(w[0] for w in words[:2]).upper() or "?"
    return f'<div class="cover gen {size}" style="background:linear-gradient(135deg,{a},{b})" role="img" aria-label="Generated cover for {e(s["name"])}"><span>{e(ini)}</span></div>'

def badges(s):
    out = ""
    if s["featured"]: out += '<span class="badge feat">Featured</span>'
    elif s["promote_requested"]: out += '<span class="badge req">Promotion requested</span>'
    if s["demo"]: out += '<span class="badge demo">Demo</span>'
    return out

def wave_bars(seed, n=48):
    x = (int(seed or 1) * 17 + 3) & 0x7fffffff
    parts = []
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7fffffff
        height = 22 + (x + i * 17) % 78
        parts.append(f'<i style="height:{height}%"></i>')
    return "".join(parts)

def wave_svg(seed, n, cls):
    x = int(seed) * 13 + 5
    rects = []
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7fffffff
        height = 10 + (x % 80)
        y = (100 - height) / 2
        rects.append(f'<rect x="{i * 6}" y="{y:.1f}" width="3" height="{height}" rx="1.5"/>')
    return f'<svg class="{cls}" viewBox="0 0 {n * 6} 100" preserveAspectRatio="none" aria-hidden="true">{"".join(rects)}</svg>'

def stable_seed(text):
    n = 0
    for ch in str(text or ""):
        n = (n * 33 + ord(ch)) & 0x7fffffff
    return n or 1

def player_box(title, audio, seed, note=""):
    bars = wave_bars(seed or 1)
    if not audio:
        message = note or "No playable episode found in this feed yet."
        return f'''<div class="player is-empty"><button class="play" type="button" disabled aria-label="No audio available"><span class="play-icon" aria-hidden="true"></span></button><div class="player-copy"><strong>{e(message)}</strong></div><div class="wave" aria-hidden="true"><div class="bars bars-dim">{bars}</div></div></div>'''
    return f'''<div class="player" data-sting="/static/podcast_stream_audio_logo.mp3" data-audio="{e(audio)}" data-title="{e(title)}"><button class="play" type="button" aria-label="Play {e(title)}"><span class="play-icon" aria-hidden="true"></span></button><div class="player-copy"><strong class="player-title">{e(title)}</strong><span class="player-time">0:00 / –:–</span></div><div class="wave" role="slider" tabindex="0" aria-label="Playback progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="bars bars-dim">{bars}</div><div class="bars bars-hot">{bars}</div><span class="scrub"></span></div><p class="player-live sr" aria-live="polite"></p></div><p class="player-hint">First play starts with a short intro, then the episode.</p>'''

def cta_band():
    return f'''<section class="cta-band">{wave_svg(4, 36, "cta-wave")}<div class="cta-copy"><h2>Got a show? <span>Put it on the chart.</span></h2><a class="btn" href="/submit">Submit your show</a></div>{wave_svg(9, 36, "cta-wave")}</section>'''

def weekly_map():
    with db() as c:
        return {r["show_id"]: r["n"] for r in c.execute(
            "SELECT show_id, COUNT(*) AS n FROM boosts WHERE created >= datetime('now','-7 days') GROUP BY show_id")}

def boost_series(show_id):
    from datetime import datetime, timedelta
    with db() as c:
        rows = c.execute("""SELECT date(created) AS day, COUNT(*) AS n FROM boosts
            WHERE show_id=? AND created >= datetime('now','-7 days') GROUP BY day""", (show_id,)).fetchall()
        start, today = c.execute("SELECT date('now','-6 days'), date('now')").fetchone()
    counts = {r["day"]: r["n"] for r in rows}
    first = datetime.fromisoformat(start).date()
    last = datetime.fromisoformat(today).date()
    earlier = [datetime.fromisoformat(day).date() for day in counts if day < start]
    if earlier:
        first = min([first] + earlier)
    series, day = [], first
    while day <= last:
        series.append((day.isoformat(), counts.get(day.isoformat(), 0)))
        day += timedelta(days=1)
    return series

def history_chart(series):
    total = sum(n for _, n in series)
    if not series or total == 0:
        return '<p class="chart-empty">No boosts in the last 7 days.</p>'
    running, cum = 0, []
    for day, n in series:
        running += n
        cum.append((day, running))
    width, height = 320, 150
    left, right, top, bottom = 30, 12, 16, 24
    peak = max(v for _, v in cum) or 1
    count = len(cum)
    inner_w, inner_h = width - left - right, height - top - bottom
    def point(i, value):
        x = left if count == 1 else left + inner_w * i / (count - 1)
        y = top + inner_h * (1 - value / peak)
        return x, y
    pts = [point(i, value) for i, (_, value) in enumerate(cum)]
    ymin, ymax = top, height - bottom
    def smooth(points):
        if len(points) == 1:
            return f"M{points[0][0]:.1f},{points[0][1]:.1f}"
        d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
        for i in range(len(points) - 1):
            p0 = points[i - 1] if i else points[i]
            p1, p2 = points[i], points[i + 1]
            p3 = points[i + 2] if i + 2 < len(points) else p2
            c1y = min(ymax, max(ymin, p1[1] + (p2[1] - p0[1]) / 6))
            c2y = min(ymax, max(ymin, p2[1] - (p3[1] - p1[1]) / 6))
            d += f" C{p1[0] + (p2[0] - p0[0]) / 6:.1f},{c1y:.1f} {p2[0] - (p3[0] - p1[0]) / 6:.1f},{c2y:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        return d
    line = smooth(pts)
    area = line + f" L{pts[-1][0]:.1f},{ymax:.1f} L{pts[0][0]:.1f},{ymax:.1f} Z"
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    def xlabel(iso, last):
        if last: return "Today"
        _year, month, day = (int(part) for part in iso.split("-"))
        return f"{day} {months[month - 1]}"
    indexes = set(range(0, count, max(1, (count - 1) // 3)))
    indexes.add(count - 1)
    labels = "".join(
        f'<text x="{pts[i][0]:.1f}" y="{height - 6}" text-anchor="middle">{e(xlabel(cum[i][0], i == count - 1))}</text>'
        for i in sorted(indexes))
    end_x, end_y = pts[-1]
    anchor, label_x = ("end", end_x - 8) if end_x > left + 36 else ("start", end_x + 8)
    return f'''<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative boosts over the last 7 days, ending at {cum[-1][1]}"><defs><linearGradient id="boostfill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b8d818" stop-opacity=".55"/><stop offset="1" stop-color="#b8d818" stop-opacity="0"/></linearGradient></defs><line class="grid" x1="{left}" y1="{top}" x2="{width - right}" y2="{top}"/><line class="grid" x1="{left}" y1="{ymax}" x2="{width - right}" y2="{ymax}"/><path class="chart-area" fill="url(#boostfill)" d="{area}"/><path class="chart-line" d="{line}"/><circle class="chart-dot" cx="{end_x:.1f}" cy="{end_y:.1f}" r="4"/><text class="chart-end" x="{label_x:.1f}" y="{max(top + 4, end_y - 8):.1f}" text-anchor="{anchor}">{cum[-1][1]}</text><text x="2" y="{top + 8}">{peak}</text><text x="2" y="{ymax}">0</text>{labels}</svg><p class="chart-caption">Cumulative boosts · last 7 days</p>'''

def card(s, view="overall"):
    if view == "rising": metric = f'{s["weekly_boosts"]} boosts this week'
    elif view == "new": metric = listed_date(s["created"])
    else: metric = f'{s["popularity"]} pts'
    return f'''<a class="showrow" href="/show/{s["id"]}">{cover(s,"sm")}<div class="showrow-body"><div class="badges">{badges(s)}</div><strong class="showrow-name">{e(s["name"])}</strong><p class="meta"><span class="pill">{e(s["category"])}</span><span>#{s["rank"]} · {e(metric)}</span></p><p class="desc">{e((s["description"] or "")[:140])}</p></div><span class="go" aria-hidden="true">&rsaquo;</span></a>'''

def page(title, body, active="", scripts="", cta=True, q=""):
    links = "".join(f'<a href="{href}" class="{"on" if active==key else ""}">{label}</a>'
                    for label, href, key in (("Discover","/","Discover"),("Charts","/charts","Charts"),("Categories","/browse","Categories")))
    band = f'<div class="wrap">{cta_band()}</div>' if cta else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#07090e"><title>{e(title)} · Podcast Stream</title><link rel="stylesheet" href="/static/style.css"><link rel="icon" href="/static/logo.svg"></head><body>
<header class="top"><div class="wrap bar"><a class="brand" href="/"><span class="dot" aria-hidden="true"></span>Podcast Stream</a>
<nav>{links}</nav>
<form class="search" action="/" role="search"><label class="sr" for="q">Search shows</label><button type="submit" aria-label="Search"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 16l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button><input id="q" name="q" value="{e(q)}" placeholder="Search"></form>
<a class="btn" href="/submit">Submit your show</a></div></header>
<main><div class="wrap">{body}</div></main>
{band}
<footer><div class="wrap"><div class="foot"><a class="brand" href="/"><span class="dot" aria-hidden="true"></span>Podcast Stream</a>
<nav><a href="/">Discover</a><a href="/charts">Charts</a><a href="/browse">Categories</a><a href="/submit">Submit your show</a><a href="/about">About</a><a href="#legal">Privacy</a><a href="#legal">Terms</a></nav>
<p class="copy">© 2026 Podcast Stream</p></div>
<p class="legal" id="legal">Listings are user-submitted and not verified or endorsed by Podcast Stream. Featured and promoted spots are always labeled and never change chart rank. No payments are processed in this preview. Show names and artwork belong to their owners.</p></div></footer>
<script src="/static/show.js" defer></script>{scripts}</body></html>'''

def chips(base, current, scroll=False):
    out = f'<a class="chip {"on" if not current else ""}" href="{base}">All</a>'
    for c in CATEGORIES:
        out += f'<a class="chip {"on" if c==current else ""}" href="{base}?category={urllib.parse.quote(c)}">{e(c)}</a>'
    return f'<div class="chips{" scroll" if scroll else ""}" aria-label="Categories">{out}</div>'

def rankline(s):
    return f'''<div class="rankrow"><a class="rankmain" href="/show/{s["id"]}"><span class="pos">{s["rank"]}</span>{cover(s,"xs")}<span class="nm">{e(s["name"])}</span><span class="pill">{e(s["category"])}</span><span class="pts">{s["popularity"]} pts</span></a><form method="post" action="/show/{s["id"]}/boost"><button class="up" type="submit" aria-label="Boost {e(s["name"])}">▲</button></form></div>'''

def spot_card(s, view):
    pill = listed_date(s["created"], with_zone=False) if view == "new" else f'{s.get("weekly_boosts") or 0} boosts this week'
    return f'''<a class="rankcard" href="/show/{s["id"]}"><div class="rankcard-art">{cover(s)}</div><span class="ranknum">#{s["rank"]}</span><span class="scrim"></span><span class="rankcard-info"><strong>{e(s["name"])}</strong><em>{e(s["category"])}</em><span class="boostpill">{e(pill)}</span></span></a>'''

def feature_strip(rows):
    items = [r for r in rows if r["featured"] or r["promote_requested"]]
    if not items: return ""
    heading = "Featured" if any(r["featured"] for r in items) else "Promotion requested"
    bits = []
    for r in items:
        badge = '<span class="badge req">Promotion requested</span>' if r["promote_requested"] and not r["featured"] else ""
        bits.append(f'<a class="featitem" href="/show/{r["id"]}">{cover(r,"sm")}<span><strong>{e(r["name"])}</strong><small>{e(r["category"])}</small>{badge}</span></a>')
    return f'''<section class="featuredstrip" aria-label="Featured shows"><div class="flag"><span class="star" aria-hidden="true">★</span><span><b>{heading}</b><small>Labeled spots. They never change rank.</small></span></div>{"".join(bits)}</section>'''

def side_link(s, weeks):
    n = weeks.get(s["id"], 0)
    delta = f' · <span class="delta">+{n} boosts</span>' if n else ""
    return f'''<a class="siderow" href="/show/{s["id"]}">{cover(s,"xs")}<span><strong>{e(s["name"])}</strong><small>{s["popularity"]} pts{delta}</small></span><span class="go" aria-hidden="true">&rsaquo;</span></a>'''

def home(q, panel="rising"):
    rows = ranked()
    if q:
        ql = q.lower()
        res = [r for r in rows if ql in r["name"].lower() or ql in (r["description"] or "").lower() or ql in (r["category"] or "").lower()]
        body = f'<p class="eyebrow">Search</p><h1>Results for “{e(q)}”</h1><p class="muted">{len(res)} result(s)</p>'
        body += f'<div class="rows">{"".join(card(r) for r in res)}</div>' if res else '<p class="empty">No shows match. Try another word or <a href="/browse">browse categories</a>.</p>'
        return page("Search", body, "Discover", q=q)
    panel = panel if panel in ("rising", "new") else "rising"
    spots = chart_rows(view=panel)[:5]
    note = "Fresh momentum · last 7 days" if panel == "rising" else "Newest listings · just added"
    cards = "".join(spot_card(r, panel) for r in spots) or '<p class="empty">Nothing in this view yet.</p>'
    tabs = "".join(
        f'<a class="{"on" if panel==key else ""}" href="{href}"{" aria-current=\"page\"" if panel==key else ""}>{label}</a>'
        for key, label, href in (("rising","Rising","/"), ("new","New","/?panel=new")))
    mid = (len(rows) + 1) // 2
    body = f'''<section class="hero"><div class="hero-copy"><p class="eyebrow">{len(rows)} shows · ranked by listener boosts</p>
<h1>Discover podcasts by rank, category, and popularity.</h1>
<p class="lede">Browse by category, follow honest charts, and post your own channel. Featured spots are labeled and never buy a higher rank.</p>
<div class="hero-actions"><a class="btn" href="/charts">Explore charts</a><a class="btn btn-line" href="/submit">Submit your show</a></div></div>
{wave_svg(5, 78, "hero-wave")}</section>
{chips("/browse", None)}
<section class="spotpanel" aria-label="Rising and new shows"><div class="spot-side"><nav class="spot-tabs" aria-label="Chart spotlight">{tabs}</nav><p class="spot-note">{e(note)}</p></div><div class="spot-cards">{cards}</div></section>
{feature_strip(rows)}
<section class="board" aria-label="Shows people are boosting"><div class="board-intro">{wave_svg(11, 28, "mini-wave")}<h2>Shows people<span class="h-break"> </span>are boosting</h2></div><div class="rankcol">{"".join(rankline(r) for r in rows[:mid])}</div><div class="rankcol">{"".join(rankline(r) for r in rows[mid:])}</div></section>'''
    return page("Discover podcasts", body, "Discover", q=q)

def browse(cat):
    cat = cat if cat in CATEGORIES else None
    rows = [r for r in ranked() if not cat or r["category"]==cat]
    rows.sort(key=lambda r: r["name"].lower())
    g = "".join(card(r) for r in rows) or '<p class="empty">No shows in this category yet — <a href="/submit">be the first</a>.</p>'
    return page("Browse", f'<p class="eyebrow">Categories</p><h1>Browse {e(cat) if cat else "all categories"}</h1><p class="muted">Alphabetical within the category. Chart rank is shown on each show and never changes for a featured spot.</p>{chips("/browse",cat)}<div class="rows">{g}</div>', "Categories")

def chart_rows(category=None, view="overall"):
    """Only listening activity or listing date determines rank, never promotion."""
    if view == "overall": return ranked(category)
    with db() as c:
        if view == "rising":
            rows = [dict(r) for r in c.execute("""
                SELECT s.*, recent.weekly_boosts FROM shows s
                JOIN (SELECT show_id, COUNT(*) AS weekly_boosts FROM boosts
                      WHERE created >= datetime('now','-7 days') GROUP BY show_id) recent
                  ON recent.show_id=s.id
                ORDER BY recent.weekly_boosts DESC, s.popularity DESC, s.id ASC
            """)]
        else:
            rows = [dict(r) for r in c.execute("SELECT * FROM shows ORDER BY created DESC, id DESC")]
    for i, row in enumerate(rows, 1): row["rank"] = i
    if category:
        rows = [r for r in rows if r["category"] == category]
        for i, row in enumerate(rows, 1): row["cat_rank"] = i
    return rows

def chart_url(view="overall", category=None):
    params = {}
    if view in ("rising", "new"): params["view"] = view
    if category: params["category"] = category
    return "/charts" + ("?" + urllib.parse.urlencode(params) if params else "")

def listed_date(value, with_zone=True):
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        text = stamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d %b %Y")
        return text + (" MYT" if with_zone else "")
    except (ValueError, TypeError): return "Date unavailable"

def charts(cat, view="overall"):
    cat = cat if cat in CATEGORIES else None
    view = view if view in ("overall", "rising", "new") else "overall"
    rows = chart_rows(cat, view)
    titles = {"overall": "Overall chart", "rising": "Rising this week", "new": "New shows"}
    descriptions = {
        "overall": "Rank 1 = highest all-time popularity. Popularity grows when listeners boost a show. Promotion never changes rank.",
        "rising": "Ranked by boosts in the last 7 days. A new show can climb even if its all-time score is small.",
        "new": "Newest listings first. Discover a show before everyone else does. Promotion never changes the order."}
    headings = {"overall": "Popularity", "rising": "Boosts this week", "new": "Listed"}
    switch = '<nav class="chart-views" aria-label="Chart view">' + "".join(
        f'<a class="chip {"on" if view==key else ""}" href="{e(chart_url(key,cat))}" {"aria-current=\"page\"" if view==key else ""}>{label}</a>'
        for key, label in (("overall", "Overall"), ("rising", "Rising"), ("new", "New"))) + '</nav>'
    filters = '<div class="chips" aria-label="Filter by category">' + "".join(
        f'<a class="chip {"on" if cat==category else ""}" href="{e(chart_url(view,category))}">{e(category or "All")}</a>'
        for category in [None] + CATEGORIES) + '</div>'
    tr = ""
    for row in rows:
        score = listed_date(row["created"]) if view == "new" else row["weekly_boosts"] if view == "rising" else row["popularity"]
        tr += f'<tr><td class="rk">{row["cat_rank"] if cat else row["rank"]}</td><td><a href="/show/{row["id"]}" class="rowlink">{cover(row,"xs")}{e(row["name"])}</a> {badges(row)}</td><td>{e(row["category"])}</td><td class="num {"listed-date" if view=="new" else ""}">{e(str(score))}</td></tr>'
    table = f'<div class="tablewrap"><table><thead><tr><th>Rank</th><th>Show</th><th>Category</th><th class="num">{headings[view]}</th></tr></thead><tbody>{tr}</tbody></table></div>' if rows else (
        '<p class="empty">No boosts in this category in the last 7 days. <a href="/browse">Browse shows</a> or <a href="/submit">submit a podcast</a>.</p>' if view == "rising" else '<p class="empty">No shows in this category yet. <a href="/submit">Submit a podcast</a>.</p>')
    category_label = f'<p class="eyebrow chart-category">{e(cat)}</p>' if cat else ""
    return page("Charts", f'<p class="eyebrow">Charts</p><h1>{titles[view]}</h1><p class="muted">{descriptions[view]}</p>{switch}{category_label}{filters}{table}', "Charts")

def show(sid):
    rows = ranked(); s = next((r for r in rows if r["id"]==sid), None)
    if not s: return None
    is_demo = s["demo"] or (urllib.parse.urlparse(s["rss"]).hostname or "").lower() == "example.com"
    if not is_demo and not s.get("latest_audio"):
        meta = fetch_rss(s["rss"])
        if meta:
            save_episode(sid, meta)
            for key, value in zip(("latest_title", "latest_audio", "latest_published", "latest_duration", "latest_description", "episode_count"), episode_values(meta)): s[key] = value
    latest = {k: s.get("latest_" + k) for k in ("title", "audio", "published", "duration", "description")}
    weeks = weekly_map()
    if is_demo:
        player = player_box("", "", s["id"], "Demo listing — no live audio feed.")
        episode_extra = ""
    elif latest.get("audio"):
        player = player_box(latest.get("title") or "Latest episode", latest["audio"], s["id"])
        details = episode_details(latest)
        episode_extra = f'<p class="ep-meta">{e(details)}</p>' if details else ""
    else:
        player = player_box("", "", s["id"], "No playable episode found in this feed yet.")
        episode_extra = ""
    catrank = [r["id"] for r in rows if r["category"]==s["category"]].index(sid)+1
    episodes = f'<span><b>{int(s["episode_count"])}</b> episodes</span>' if s.get("episode_count") else ""
    links = []
    if s.get("website"): links.append(f'<a href="{e(s["website"])}" target="_blank" rel="noopener nofollow">Listen / visit website</a>')
    if s.get("rss"): links.append(f'<a href="{e(s["rss"])}" target="_blank" rel="noopener nofollow">RSS feed</a>')
    quiet = f'<p class="quietlinks">{"".join(links)}</p>' if links else ""
    others = sorted((r for r in rows if r["category"]==s["category"] and r["id"]!=sid), key=lambda r: (-r["popularity"], r["id"]))
    more_html = "".join(side_link(r, weeks) for r in others[:4]) or f'<p class="empty">No other shows in {e(s["category"])} yet.</p>'
    rising = [r for r in chart_rows(view="rising") if r["id"]!=sid][:4]
    rising_rows = "".join(side_link(r, weeks) for r in rising) or '<p class="empty">No boosts this week yet.</p>'
    return page(s["name"], f'''<div class="show-layout"><div class="show-main"><div class="show-hero">{cover(s,"lg",lazy=False)}<div class="show-info">
<p class="crumb"><a href="/charts">Charts</a> <span aria-hidden="true">/</span> <a href="/browse?category={urllib.parse.quote(s["category"])}">{e(s["category"])}</a></p>
<div class="badges">{badges(s)}</div>
<p class="rankbig">#{s["rank"]}</p>
<h1>{e(s["name"])}</h1>
<p><a class="pill" href="/browse?category={urllib.parse.quote(s["category"])}">{e(s["category"])}</a></p>
<p class="lead">{e(s["description"])}</p>
<p class="byline">by {e(s["creator"])} · #{catrank} in {e(s["category"])}</p>
<div class="statrow"><span><b>{s["popularity"]}</b> pts</span><span class="upstat"><b>+{weeks.get(sid, 0)}</b> boosts this week</span>{episodes}</div>
<div class="actions"><form method="post" action="/show/{sid}/boost"><button class="btn" type="submit"><span aria-hidden="true">▲</span> Boost this show</button></form>
<button class="btn ghost" type="button" id="share-show" data-title="{e(s["name"])}"><span class="share-label">Share</span></button></div>
{quiet}</div></div>{player}{episode_extra}</div>
<aside class="show-side"><section class="side-card"><h2>Chart history</h2>{history_chart(boost_series(sid))}</section>
<section class="side-card"><h2>More in {e(s["category"])}</h2>{more_html}</section>
<section class="side-card"><h2>Rising this week <span class="rise" aria-hidden="true">▲</span></h2>{rising_rows}</section></aside></div>''')

def submit_form(v=None, errors=None, ok=None, preview=None, feed_error=False):
    v = v or {}; errors = errors or []
    opts = "".join(f'<option {"selected" if v.get("category")==c else ""}>{e(c)}</option>' for c in CATEGORIES)
    err = f'<div class="alert" role="alert"><b>Please fix:</b><ul>{"".join(f"<li>{e(x)}</li>" for x in errors)}</ul></div>' if errors else ""
    f = lambda n,l,t="text",req=True,ph="": f'<label>{l}{" *" if req else " <small>(optional)</small>"}<input type="{t}" name="{n}" value="{e(v.get(n))}" {"required" if req else ""} placeholder="{ph}"></label>'
    status = "Could not read that feed. Check the RSS URL or fill in the details yourself." if feed_error else (f'Filled from the feed — you can edit anything before submitting. {preview["episode_count"]} episodes found.' if preview else "Paste an RSS URL to import your show and preview its latest episode.")
    return page("Submit", f'''<p class="eyebrow">Submit</p><h1>Post &amp; promote your <em>podcast</em></h1><p class="muted">Free listing. Start with your RSS feed, then review and edit your details.</p>{err}
<form class="form" id="submit-show" method="post" action="/submit">
<div class="rss-import full"><label for="rss-url">RSS feed URL *</label><div class="rss-controls"><input id="rss-url" type="url" name="rss" value="{e(v.get('rss'))}" required placeholder="https://example.com/feed.xml"><button type="button" class="btn fill-feed" id="fill-feed">Fill from feed</button></div>
<p id="feed-status" class="feed-status {'error' if feed_error else 'success' if preview else ''}" role="status" aria-live="polite">{e(status)}</p></div>
<div id="feed-preview" class="feed-preview full" {'' if preview else 'hidden'}>{feed_preview(preview)}</div>
{f("name","Show name",ph="My Great Show")}
<label>Category *<select name="category" required><option value="">Choose…</option>{opts}</select></label>
<label class="full">Short description *<textarea name="description" maxlength="400" required rows="3">{e(v.get("description"))}</textarea></label>
{f("cover","Cover image URL","url",False,"https://…/cover.jpg")}{f("creator","Creator name")}{f("website","Website","url",False,"https://…")}
<label class="check full"><input type="checkbox" name="promote" {"checked" if v.get("promote") else ""}> Request a featured / promote spot <small>(shown as “Promotion requested”; no payment in this preview; never changes rank)</small></label>
<button class="btn full">Submit podcast</button></form>''', "Submit", '<script src="/static/submit.js" defer></script>', False)

URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*\.[^\s]+$", re.I)
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

def valid_url(url):
    return isinstance(url, str) and bool(URL_RE.fullmatch(url)) and urllib.parse.urlparse(url).scheme.lower() in ("http", "https")

def plain_text(value, limit):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()[:limit]

def podcast_xml(data):
    """Parse a podcast document. A feed larger than the read cap is closed at the last full episode."""
    try:
        return ET.fromstring(data), True
    except ET.ParseError:
        cut = data.rfind(b"</item>")
        if cut == -1: raise
        return ET.fromstring(data[:cut + len(b"</item>")] + b"</channel></rss>"), False

def fetch_rss(url):
    if not valid_url(url): return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"PodcastStream/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(20_000_001)
        complete = len(data) < 20_000_001
        if not complete: data = data[:20_000_000]
        root, well_formed = podcast_xml(data)
        ch = root.find("channel")
        if ch is None: return {}
        it = ch.find(ITUNES + "image")
        image = (it.get("href", "") if it is not None else "") or ch.findtext("image/url") or ""
        creator = ch.findtext(ITUNES + "author") or ch.findtext("managingEditor") or ""
        if "@" in creator:
            from email.utils import parseaddr
            name, address = parseaddr(creator)
            creator = name or re.sub(r"[\w.+-]+@[\w.-]+", "", creator).strip(" <>()[\x5d,;")
        items = ch.findall("item")
        latest = {}
        for item in items:
            audio = ""
            for enc in item.findall("enclosure"):
                candidate = enc.get("url", "").strip()
                path = urllib.parse.urlparse(candidate).path.lower()
                if valid_url(candidate) and (enc.get("type", "").lower().startswith("audio/") or path.endswith((".mp3", ".m4a", ".ogg", ".aac"))):
                    audio = candidate
                    break
            if audio:
                latest = {"title": (item.findtext("title") or "Latest episode").strip(), "audio": audio,
                          "published": (item.findtext("pubDate") or "").strip(),
                          "duration": (item.findtext(ITUNES + "duration") or "").strip(),
                          "description": plain_text(item.findtext("description") or item.findtext(ITUNES + "summary"), 280)}
                break
        return {"name": (ch.findtext("title") or "").strip(),
                "description": plain_text(ch.findtext("description"), 400),
                "cover": image.strip() if valid_url(image.strip()) else "",
                "website": (ch.findtext("link") or "").strip(),
                "creator": plain_text(creator, 80), "episode_count": len(items) if complete and well_formed else None, "latest": latest}
    except Exception: return {}

def episode_values(meta):
    latest = meta.get("latest") or {}
    return tuple(latest.get(k, "") for k in ("title", "audio", "published", "duration", "description")) + (meta.get("episode_count"),)

def save_episode(sid, meta):
    with db() as c:
        c.execute("UPDATE shows SET latest_title=?,latest_audio=?,latest_published=?,latest_duration=?,latest_description=?,episode_count=? WHERE id=?", episode_values(meta) + (sid,))

def episode_panel(latest):
    if not latest.get("audio"): return ""
    return f'''<section class="episode-panel" aria-label="Latest episode">
<p class="eyebrow">Latest episode</p><h2>{e(latest.get("title") or "Latest episode")}</h2>
<p class="episode-meta">{e(episode_details(latest))}</p>
<p class="episode-description">{e(latest.get("description"))}</p>
{player_box(latest.get("title") or "Latest episode", latest["audio"], stable_seed(latest.get("audio")))}</section>'''

def episode_details(latest):
    # Publication times are normalized to Malaysia time, matching the preview client.
    published = latest.get("published", "")
    try:
        from email.utils import parsedate_to_datetime
        from zoneinfo import ZoneInfo
        from datetime import timezone
        dt = parsedate_to_datetime(published)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
        published = dt.strftime("%d %b %Y · %H:%M MYT")
    except (ValueError, TypeError, OverflowError): pass
    duration = str(latest.get("duration") or "")
    if duration.isdigit():
        seconds = int(duration)
        duration = f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"
    return " · ".join(str(x) for x in (published, duration) if x)

def feed_preview(meta):
    if not meta: return ""
    image = f'<img class="feed-cover" src="{e(meta.get("cover"))}" alt="Podcast cover">' if meta.get("cover") else ""
    return f'<div class="feed-preview-head">{image}<div><p class="eyebrow">Feed preview</p><h2>{e(meta.get("name"))}</h2><p class="muted">{e(meta.get("episode_count"))} episodes in this feed</p></div></div>' + episode_panel(meta.get("latest") or {})

def submit_prefill(rss):
    if not rss: return submit_form()
    meta = fetch_rss(rss)
    values = {k: meta.get(k, "") for k in ("name", "description", "cover", "website", "creator")}
    values["rss"] = rss
    return submit_form(values, preview=meta, feed_error=not bool(meta))

def do_submit(v):
    v = {k:(v.get(k,[""])[0]).strip() for k in ["name","rss","category","description","cover","creator","website","promote"]}
    errors = []
    for k,l in [("name","Show name"),("rss","RSS feed URL"),("category","Category"),("description","Description")]:
        if not v[k]: errors.append(f"{l} is required.")
    if v["rss"] and not valid_url(v["rss"]): errors.append("RSS feed URL must be a valid http(s) URL, e.g. https://example.com/feed.xml")
    for k in ("cover","website"):
        if v[k] and not valid_url(v[k]): errors.append(f"{k.title()} must be a valid http(s) URL.")
    if v["category"] and v["category"] not in CATEGORIES: errors.append("Pick a category from the list.")
    if errors: return None, submit_form(v, errors)
    meta = fetch_rss(v["rss"])
    for k in ("cover", "website"):
        if not v[k] and valid_url(meta.get(k, "")): v[k] = meta[k]
    if not v["creator"]: v["creator"] = meta.get("creator", "")
    if not v["creator"]: return None, submit_form(v, ["Creator name is required."])
    with db() as c:
        cur = c.execute("INSERT INTO shows(name,rss,category,description,cover,creator,website,popularity,promote_requested,latest_title,latest_audio,latest_published,latest_duration,latest_description,episode_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v["name"][:120],v["rss"],v["category"],v["description"][:400],v["cover"] or None,v["creator"][:80],v["website"] or None,1,1 if v["promote"] else 0) + episode_values(meta))
        return cur.lastrowid, None

# Random salt for the current UTC day only. Replaced at midnight and never written
# to SQLite, so yesterday's visitor hashes cannot be rebuilt from the database.
_daily_salt = {"day": "", "value": ""}
_salt_lock = threading.Lock()
NOISE_UA = re.compile(
    r"twitterbot|facebookexternalhit|googlebot|bingbot|slackbot|discordbot|linkedinbot"
    r"|python-requests|curl|bot|crawler|spider|preview",
    re.IGNORECASE)
NOT_FOUND_PAGE = page("Not found", '<h1>Not found</h1><p><a href="/">Back home</a></p>')

def daily_salt(today=None):
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _salt_lock:
        if _daily_salt["day"] != today or not _daily_salt["value"]:
            _daily_salt["day"] = today
            _daily_salt["value"] = secrets.token_hex(16)
        return _daily_salt["value"]

def visitor_hash(salt, ip, user_agent):
    material = f"{salt}{ip}{user_agent}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(material).hexdigest()

def client_ip(headers, socket_host):
    forwarded = ""
    if headers is not None:
        forwarded = headers.get("X-Forwarded-For", "") or ""
    first = forwarded.split(",")[0].strip() if forwarded else ""
    return first or (socket_host or "").strip()

def clean_referrer(value):
    """Keep the referrer URL with the query string and fragment removed."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urllib.parse.urlsplit(value[:2000])
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme.lower(), host, parts.path or "", "", ""))[:500]

def request_path(raw):
    path = urllib.parse.urlsplit(raw or "/").path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path[:500]

def is_noise(method, headers):
    if (method or "").upper() in ("HEAD", "OPTIONS"):
        return True
    if headers is None:
        return False
    if headers.get("Render-Health-Check"):
        return True
    ua = headers.get("User-Agent", "") or ""
    if ua.lower().startswith("render/"):
        return True
    return bool(NOISE_UA.search(ua))

def record_page_view(handler):
    """Log one HTML page view. Any failure is ignored so the page still loads."""
    try:
        if (handler.command or "").upper() != "GET":
            return
        path = request_path(handler.path)
        if path.startswith("/static/") or path.startswith("/api/"):
            return
        headers = handler.headers
        if is_noise(handler.command, headers):
            return
        socket_host = handler.client_address[0] if handler.client_address else ""
        ip = client_ip(headers, socket_host)
        ua = (headers.get("User-Agent", "") if headers else "") or ""
        referrer = clean_referrer(headers.get("Referer", "") if headers else "")
        digest = visitor_hash(daily_salt(), ip, ua)
        viewed = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB, timeout=0.5)
        try:
            conn.execute(
                "INSERT INTO page_views(path, viewed_at, referrer, visitor_hash) VALUES(?,?,?,?)",
                (path, viewed, referrer, digest))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return

def configured_stats_token():
    return (os.environ.get("STATS_TOKEN") or "").strip()

def presented_secret(headers):
    """Token from Basic password or Bearer. Query strings are never read."""
    if headers is None:
        return ""
    raw = headers.get("Authorization", "") or ""
    scheme, _, rest = raw.partition(" ")
    scheme, rest = scheme.lower(), rest.strip()
    if scheme == "bearer":
        return rest
    if scheme != "basic" or not rest:
        return ""
    try:
        decoded = base64.b64decode(rest, validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return ""
    _user, sep, password = decoded.partition(":")
    return password if sep else ""

def secrets_match(provided, expected):
    if not provided or not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest())

def visit_stats():
    def window(conn, where):
        views = conn.execute(f"SELECT COUNT(*) FROM page_views WHERE {where}").fetchone()[0]
        visitors = conn.execute(f"SELECT COUNT(DISTINCT visitor_hash) FROM page_views WHERE {where}").fetchone()[0]
        return {"views": views, "visitors": visitors}
    conn = sqlite3.connect(DB, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        week = "viewed_at >= datetime('now', '-7 days')"
        pages = [dict(row) for row in conn.execute(
            f"""SELECT path, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
                FROM page_views WHERE {week} GROUP BY path ORDER BY views DESC, path ASC LIMIT 15""")]
        referrers = [dict(row) for row in conn.execute(
            f"""SELECT referrer, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
                FROM page_views WHERE {week} AND referrer != ''
                GROUP BY referrer ORDER BY views DESC, referrer ASC LIMIT 15""")]
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "today": window(conn, "viewed_at >= date('now')"),
            "last_7_days": window(conn, week),
            "all_time": window(conn, "1=1"),
            "top_pages": pages,
            "top_referrers": referrers,
            "uniques": "Distinct hashes for each UTC day. A visitor who returns on a later day counts again in the 7-day and all-time totals, because that day's salt has been discarded.",
        }
    finally:
        conn.close()

def stats_page(report):
    def card(label, block):
        return f'''<article class="statcard"><span>{e(label)}</span><b>{int(block["views"])}</b><small>{int(block["visitors"])} unique</small></article>'''
    def table(items, key, heading):
        if not items:
            return '<p class="empty">Nothing in the last 7 days.</p>'
        body = "".join(
            f'<tr><td>{e(item[key])}</td><td class="num">{int(item["views"])}</td><td class="num">{int(item["visitors"])}</td></tr>'
            for item in items)
        return f'<div class="tablewrap"><table><thead><tr><th>{e(heading)}</th><th class="num">Views</th><th class="num">Unique</th></tr></thead><tbody>{body}</tbody></table></div>'
    body = f'''<p class="eyebrow">Stats</p><h1>Visitors</h1>
<p class="muted">HTML pages only. Static files, API calls, bots, and Render health checks are skipped. Unique visitors are daily hashes, so a return visit on another UTC day counts again in the 7-day and all-time totals.</p>
<div class="stats-grid">{card("Today (UTC)", report["today"])}{card("Last 7 days", report["last_7_days"])}{card("All time", report["all_time"])}</div>
<div class="stats-split"><section><h2>Top pages</h2><p class="muted">Last 7 days</p>{table(report["top_pages"], "path", "Page")}</section>
<section><h2>Top referrers</h2><p class="muted">Last 7 days, query strings removed</p>{table(report["top_referrers"], "referrer", "Referrer")}</section></div>'''
    return page("Stats", body, "", "", False)

class H(BaseHTTPRequestHandler):
    def send(self, body, code=200, ctype="text/html; charset=utf-8", extra=None, log_visit=None):
        if log_visit is None:
            log_visit = self.command == "GET" and ctype.startswith("text/html")
        if log_visit:
            record_page_view(self)
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(b)
    def redirect(self, to): self.send_response(303); self.send_header("Location",to); self.end_headers()
    def log_message(self, fmt, *args):
        # Path only. The request line can carry a query string, and the socket address is an IP.
        raw = getattr(self, "path", "") or "/"
        path = urllib.parse.urlsplit(raw).path or "/"
        sys.stderr.write("%s %s %s\n" % (self.log_date_time_string(), getattr(self, "command", "-"), path))
    def serve_stats(self, path):
        expected = configured_stats_token()
        private = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
        if not expected:
            self.send(NOT_FOUND_PAGE, 404, extra=private)
            return
        if not secrets_match(presented_secret(self.headers), expected):
            self.send("Unauthorized", 401, "text/plain; charset=utf-8",
                      {**private, "WWW-Authenticate": 'Basic realm="Podcast Stream stats"'})
            return
        report = visit_stats()
        if path == "/api/stats":
            self.send(json.dumps(report), ctype="application/json; charset=utf-8", extra=private)
        else:
            self.send(stats_page(report), extra=private)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); qs = urllib.parse.parse_qs(u.query); g = lambda k: qs.get(k,[""])[0]
        p = u.path
        if p.startswith("/static/"):
            rel = urllib.parse.unquote(p[len("/static/"):]).lstrip("/")
            root = os.path.realpath(os.path.join(BASE, "static"))
            fp = os.path.realpath(os.path.join(root, rel))
            if fp.startswith(root + os.sep) and os.path.isfile(fp):
                ext = fp.rsplit(".", 1)[-1].lower()
                ct = {"css":"text/css; charset=utf-8","js":"application/javascript; charset=utf-8","svg":"image/svg+xml",
                      "png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg","webp":"image/webp",
                      "mp3":"audio/mpeg","woff2":"font/woff2","woff":"font/woff"}.get(ext, "application/octet-stream")
                with open(fp, "rb") as handle:
                    return self.send(handle.read(), ctype=ct)
            return self.send(page("Not found",'<h1>Not found</h1><p><a href="/">Back home</a></p>'), 404)
        elif p == "/": return self.send(home(g("q").strip(), g("panel")))
        elif p == "/browse": return self.send(browse(g("category")))
        elif p == "/charts": return self.send(charts(g("category"), g("view")))
        elif p == "/submit": return self.send(submit_prefill(g("rss").strip()))
        elif p == "/api/preview-rss":
            meta = fetch_rss(g("url").strip())
            return self.send(json.dumps(dict(ok=True, **meta) if meta else {"ok":False,"error":"Could not read that feed."}), ctype="application/json; charset=utf-8")
        elif p == "/about": return self.send(page("About", f'''<article class="prose"><h1>About Podcast Stream</h1><p>Podcast Stream helps people discover podcasts by category, rank, and popularity, and lets creators post and promote their channels.</p><h2>How ranking works</h2><p>Every show has a popularity score. Listeners raise it with the Boost button. Overall ranks by all-time popularity; Rising ranks by boosts in the last 7 days, so newer shows can climb without a large lifetime score. New lists shows by the date they were added, newest first. Charts are recalculated on every boost and every new listing.</p><p>The 30 shows in the opening catalog start at 0 popularity. No listener totals were invented for them. When scores tie, the earlier listing ranks higher. Those shows were inserted in the order of the <a href="{e(CATALOG_FILE["chart_url"])}">Apple Podcasts US Top Shows</a> chart updated {e(CATALOG_FILE["chart_updated"])}. Shows from the catalog that were not on that chart follow the charted shows, alphabetically, still at 0. A listener boost is the only thing that adds a point.</p><h2>Where the catalog comes from</h2><p>Titles, publishers, categories, RSS feeds, and Apple Podcasts links were read from the Apple iTunes Lookup API on 13 November 2026. Descriptions were read from each show's RSS feed. Cover art is that show's Apple artwork with the image size set to 1400×1400, after the image URL returned a real image. Listings visitors submit are kept and are not replaced by the catalog.</p><h2>Featured &amp; promoted</h2><p>Creators can request a promoted spot. Promoted shows appear in a clearly labeled strip and never get a higher chart rank for it.</p></article>''', "About"))
        elif p in ("/stats", "/api/stats"): return self.serve_stats(p)
        elif p == "/api/shows": return self.send(json.dumps(ranked()),ctype="application/json")
        else:
            m = re.fullmatch(r"/show/(\d+)",p)
            if m and (h := show(int(m.group(1)))): return self.send(h)
        self.send(NOT_FOUND_PAGE, 404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length",0) or 0)
        data = urllib.parse.parse_qs(self.rfile.read(min(n,20000)).decode(), keep_blank_values=True)
        p = urllib.parse.urlparse(self.path).path
        if p == "/submit":
            sid, err = do_submit(data)
            return self.send(err,400) if err else self.redirect(f"/show/{sid}")
        m = re.fullmatch(r"/show/(\d+)/boost",p)
        if m:
            sid = int(m.group(1))
            with db() as c:
                updated = c.execute("UPDATE shows SET popularity=popularity+1 WHERE id=?", (sid,))
                if updated.rowcount:
                    c.execute("INSERT INTO boosts(show_id) VALUES(?)", (sid,))
                else:
                    return self.send("Show not found", 404)
            return self.redirect(f"/show/{m.group(1)}")
        self.send("Not found",404)

if __name__ == "__main__":
    init(); port = int(os.environ.get("PORT","8000"))
    print(f"Podcast Stream running at http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0",port),H).serve_forever()
