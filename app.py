#!/usr/bin/env python3
"""Podcast Stream v1 - zero-dependency podcast directory (Python stdlib only)."""
import html, json, os, re, sqlite3, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "podcast_steam.db")
CATEGORIES = ["Arts","Business","Comedy","Education","Fiction","Government","Health & Fitness","History",
 "Kids & Family","Leisure","Music","News","Religion & Spirituality","Science","Society & Culture","Sports",
 "Technology","True Crime","TV & Film"]
SEED = [  # fictional demo shows
 ("Midnight Ledger","Business","Small founders talk cash flow, pricing and the mistakes nobody posts about.",412,1),
 ("Orbit Kitchen","Science","Two astronomy nerds explain the universe while cooking dinner.",389,0),
 ("Pencil & Pixel","Arts","Weekly chats with illustrators about craft, tools and creative burnout.",256,0),
 ("The Quiet Verdict","True Crime","Calm, carefully sourced retellings of forgotten court cases.",498,0),
 ("Laugh Track Lost","Comedy","Improv comedians riff on a random headline every episode.",301,1),
 ("Byte-Sized Mornings","Technology","Ten-minute tech news briefings for your commute.",455,0),
 ("Little Lantern Stories","Kids & Family","Gentle bedtime stories with original characters.",222,0),
 ("Old Roads Radio","History","Travel through history one ancient trade route at a time.",187,0),
 ("Stride Club","Health & Fitness","Beginner-friendly running coaching and recovery tips.",164,0),
 ("Reel Talkers","TV & Film","Spoiler-free film club for the week's new releases.",278,0),
 ("Bassline Diaries","Music","Indie producers break down how one track was made.",143,0),
 ("Sideline Sundays","Sports","Grassroots sports stories from local leagues.",119,0),
]
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
        if not c.execute("SELECT COUNT(*) FROM shows").fetchone()[0]:
            for n,cat,d,p,f in SEED:
                slug = re.sub(r"[^a-z0-9]+","-",n.lower()).strip("-")
                c.execute("INSERT INTO shows(name,rss,category,description,creator,website,popularity,featured,demo) VALUES(?,?,?,?,?,?,?,?,1)",
                  (n,f"https://example.com/feeds/{slug}.xml",cat,d,"Demo Creator",f"https://example.com/{slug}",p,f))
        c.execute("""CREATE TABLE IF NOT EXISTS boosts (
            id INTEGER PRIMARY KEY, show_id INTEGER NOT NULL,
            created TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("CREATE INDEX IF NOT EXISTS boosts_recent ON boosts(created, show_id)")
        # Preview-only history: never rewrite the all-time popularity totals.
        if not c.execute("SELECT COUNT(*) FROM boosts").fetchone()[0]:
            weekly = {"Pencil & Pixel":32, "Stride Club":25, "Sideline Sundays":19,
                      "Orbit Kitchen":12, "Houston We Have a Podcast":7,
                      "Laugh Track Lost":6, "Midnight Ledger":5, "Little Lantern Stories":4,
                      "Old Roads Radio":3, "Reel Talkers":3, "Bassline Diaries":2,
                      "The Quiet Verdict":2, "Byte-Sized Mornings":1}
            for row in c.execute("SELECT id,name FROM shows").fetchall():
                for i in range(weekly.get(row["name"], 0)):
                    hours = 1 + (i * 29 + row["id"] * 11) % 164
                    c.execute("INSERT INTO boosts(show_id,created) VALUES(?,datetime('now',?))",
                              (row["id"], f"-{hours} hours"))
        # The original demos were inserted together. Stagger only that tied batch;
        # real submission dates (including NASA) remain untouched on every restart.
        demo_dates = c.execute("SELECT COUNT(*),COUNT(DISTINCT created) FROM shows WHERE demo=1").fetchone()
        if demo_dates[0] > 1 and demo_dates[1] == 1:
            demos = c.execute("SELECT id FROM shows WHERE demo=1 ORDER BY id").fetchall()
            for i, row in enumerate(demos):
                days = 7 + (len(demos) - i - 1) * 4
                c.execute("UPDATE shows SET created=datetime('now',?) WHERE id=?", (f"-{days} days", row["id"]))

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

def cover(s, size=""):
    if s.get("cover"):
        return f'<img class="cover {size}" src="{e(s["cover"])}" alt="Cover art for {e(s["name"])}" loading="lazy">'
    a,b = PALETTES[s["id"] % len(PALETTES)]
    ini = "".join(w[0] for w in s["name"].split()[:2]).upper()
    return f'<div class="cover gen {size}" style="background:linear-gradient(135deg,{a},{b})" role="img" aria-label="Generated cover for {e(s["name"])}"><span>{e(ini)}</span></div>'

def badges(s):
    out = ""
    if s["featured"]: out += '<span class="badge feat">Featured</span>'
    elif s["promote_requested"]: out += '<span class="badge req">Promotion requested</span>'
    if s["demo"]: out += '<span class="badge demo">Demo</span>'
    return out

def card(s, view="overall"):
    metric = f'{s["weekly_boosts"]} boosts this week' if view == "rising" else f'{s["popularity"]} pts'
    return f'''<a class="showrow" href="/show/{s["id"]}">{cover(s,"sm")}<div class="showrow-body">
<div class="badges">{badges(s)}</div><h3>{e(s["name"])}</h3>
<p class="meta">#{s["rank"]} · {e(s["category"])} · {metric}</p>
<p class="desc">{e(s["description"][:140])}</p></div><span class="go" aria-hidden="true">&rarr;</span></a>'''

def page(title, body, active=""):
    nav = "".join(f'<a href="{h}" class="{"on" if active==k else ""}">{k}</a>' for k,h in
                  [("Home","/"),("Browse","/browse"),("Charts","/charts"),("About","/about")])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · Podcast Stream</title><link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="/static/logo.svg"></head><body>
<div class="aura" aria-hidden="true"></div>
<div class="preview">First preview · demo listings are fictional</div>
<header class="top"><a class="brand" href="/"><img src="/static/logo.svg" alt="" width="36" height="36"><span>Podcast<b>Stream</b></span></a>
<nav>{nav}</nav>
<a class="btn cta-top" href="/submit">+ Submit a show</a></header>
<main>{body}</main>
<footer><p><b>Podcast Stream</b> helps people discover podcasts by category, rank, and popularity, and lets creators post and promote their channels.</p>
<p class="legal">Listings are user-submitted and not verified or endorsed by Podcast Stream. Featured and promoted spots are always labeled and never change chart rank. No payments are processed in this preview. Show names and artwork belong to their owners.</p></footer></body></html>'''

def chips(base, current):
    out = f'<a class="chip {"on" if not current else ""}" href="{base}">All</a>'
    for c in CATEGORIES:
        out += f'<a class="chip {"on" if c==current else ""}" href="{base}?category={urllib.parse.quote(c)}">{e(c)}</a>'
    return f'<div class="chips">{out}</div>'

def home(q):
    rows = ranked()
    if q:
        ql = q.lower()
        res = [r for r in rows if ql in r["name"].lower() or ql in r["description"].lower()]
        body = f'<p class="eyebrow">Search</p><h1>Results for “{e(q)}”</h1><p class="muted">{len(res)} result(s)</p>' + searchbox(q)
        body += f'<div class="rows">{"".join(card(r) for r in res)}</div>' if res else '<p class="empty">No shows match. Try another word or <a href="/browse">browse categories</a>.</p>'
        return page("Search", body, "Home")
    feat = [r for r in rows if r["featured"] or r["promote_requested"]]
    feat_html = "".join(card(r) for r in feat) or '<p class="empty">No featured shows yet.</p>'
    listed = "".join(card(r) for r in rows)
    rising_html = "".join(card(r, "rising") for r in chart_rows(view="rising")[:5]) or '<p class="empty">No boosts this week yet. <a href="/browse">Find a show to boost</a>.</p>' 
    return page("Discover podcasts", f'''<section class="hero">
<span class="live"><span class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span> {len(rows)} shows · ranked by listener boosts</span>
<h1>Discover podcasts by rank, category, and <em>popularity</em>.</h1>
<p>Browse by category, follow honest charts, and post your own channel. Featured spots are labeled and never buy a higher rank.</p>
<div class="hero-tools">{searchbox("")}
<div class="statline">
<div><b>{len(rows)}</b><span>Shows listed</span></div>
<div><b>19</b><span>Categories</span></div>
<div><b>{len(feat)}</b><span>Featured spots</span></div>
<div><b>0</b><span>Rank for sale</span></div>
</div></div></section>
<section class="home-rising"><div class="sec-h"><div><p class="eyebrow">Fresh momentum · last 7 days</p><h2>Rising this week</h2></div><a href="/charts?view=rising">See Rising chart &rarr;</a></div>
<div class="rows compact-rising">{rising_html}</div></section>
<section><div class="sec-h"><div><p class="eyebrow">Featured &amp; promoted</p><h2>Labeled spots. They do not affect rank.</h2></div></div>
<div class="featgrid">{feat_html}</div></section>
<section><div class="sec-h"><div><p class="eyebrow">Top ranked</p><h2>Shows people are boosting.</h2></div><a href="/charts">See full chart &rarr;</a></div>
{chips("/browse", None)}
<div class="rows">{listed}</div></section>''', "Home")

def searchbox(q):
    return f'<form class="search" action="/" role="search"><label for="q" class="sr">Search podcasts</label><input id="q" name="q" value="{e(q)}" placeholder="Search shows by name or topic…"><button class="btn">Search</button></form>'

def browse(cat):
    cat = cat if cat in CATEGORIES else None
    rows = [r for r in ranked() if not cat or r["category"]==cat]
    rows.sort(key=lambda r: r["name"].lower())
    g = "".join(card(r) for r in rows) or '<p class="empty">No shows in this category yet — <a href="/submit">be the first</a>.</p>'
    return page("Browse", f'<p class="eyebrow">Library</p><h1>Browse {e(cat) if cat else "all categories"}</h1>{chips("/browse",cat)}<div class="rows">{g}</div>', "Browse")

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

def listed_date(value):
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d %b %Y") + " MYT"
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
    note = '<p class="chart-note">Preview activity is illustrative. Featured and promoted spots never buy rank.</p>' if view == "rising" else ""
    category_label = f'<p class="eyebrow chart-category">{e(cat)}</p>' if cat else ""
    return page("Charts", f'<p class="eyebrow">Charts</p><h1>{titles[view]}</h1><p class="muted">{descriptions[view]}</p>{switch}{category_label}{filters}{table}{note}', "Charts")

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
    player = '<p class="feed-note">Demo listing — no live audio feed.</p>' if is_demo else (episode_panel(latest) or '<p class="feed-note">No playable episode found in this feed yet.</p>')
    catrank = [r["id"] for r in rows if r["category"]==s["category"]].index(sid)+1
    listen = f'<a class="btn" href="{e(s["website"])}" target="_blank" rel="noopener nofollow">Listen / visit website</a>' if s["website"] else ""
    return page(s["name"], f'''<article class="show">{cover(s,"lg")}<div><div class="badges">{badges(s)}</div><h1>{e(s["name"])}</h1>
<p class="meta"><a href="/browse?category={urllib.parse.quote(s["category"])}">{e(s["category"])}</a> · by {e(s["creator"])}</p>
<div class="stats"><div><b>#{s["rank"]}</b><span>Overall rank</span></div><div><b>#{catrank}</b><span>In {e(s["category"])}</span></div><div><b id="pop">{s["popularity"]}</b><span>Popularity</span></div></div>
<p class="lead">{e(s["description"])}</p>{player}<div class="cta">{listen}
<form method="post" action="/show/{sid}/boost"><button class="btn boost">▲ Boost popularity</button></form>
<a class="btn ghost" href="{e(s["rss"])}" target="_blank" rel="noopener nofollow">RSS feed</a></div>
{f'<p class="muted">Website: <a href="{e(s["website"])}" rel="nofollow">{e(s["website"])}</a></p>' if s["website"] else ""}</div></article>''')

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
<button class="btn full">Submit podcast</button></form><script src="/static/submit.js" defer></script>''', "Submit")

URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*\.[^\s]+$", re.I)
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

def valid_url(url):
    return isinstance(url, str) and bool(URL_RE.fullmatch(url)) and urllib.parse.urlparse(url).scheme.lower() in ("http", "https")

def plain_text(value, limit):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()[:limit]

def fetch_rss(url):
    if not valid_url(url): return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"PodcastStream/0.1"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read(2_000_001)
        if len(data) > 2_000_000: return {}
        ch = ET.fromstring(data).find("channel")
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
                "creator": plain_text(creator, 80), "episode_count": len(items), "latest": latest}
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
<audio controls preload="metadata" src="{e(latest['audio'])}">Your browser does not support audio playback. <a href="{e(latest['audio'])}">Listen to the episode</a>.</audio></section>'''

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

class H(BaseHTTPRequestHandler):
    def send(self, body, code=200, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body,str) else body
        self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Content-Length",len(b)); self.end_headers(); self.wfile.write(b)
    def redirect(self, to): self.send_response(303); self.send_header("Location",to); self.end_headers()
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); qs = urllib.parse.parse_qs(u.query); g = lambda k: qs.get(k,[""])[0]
        p = u.path
        if p.startswith("/static/"):
            fp = os.path.join(BASE,"static",os.path.basename(p))
            if os.path.isfile(fp):
                ct = {"css":"text/css","js":"application/javascript; charset=utf-8","svg":"image/svg+xml","png":"image/png"}.get(fp.rsplit(".",1)[-1],"application/octet-stream")
                return self.send(open(fp,"rb").read(),ctype=ct)
        elif p == "/": return self.send(home(g("q").strip()))
        elif p == "/browse": return self.send(browse(g("category")))
        elif p == "/charts": return self.send(charts(g("category"), g("view")))
        elif p == "/submit": return self.send(submit_prefill(g("rss").strip()))
        elif p == "/api/preview-rss":
            meta = fetch_rss(g("url").strip())
            return self.send(json.dumps(dict(ok=True, **meta) if meta else {"ok":False,"error":"Could not read that feed."}), ctype="application/json; charset=utf-8")
        elif p == "/about": return self.send(page("About",'<article class="prose"><h1>About Podcast Stream</h1><p>Podcast Stream helps people discover podcasts by category, rank, and popularity, and lets creators post and promote their channels.</p><h2>How ranking works</h2><p>Every show has a popularity score. Listeners raise it with the Boost button. Overall ranks by all-time popularity; Rising ranks by boosts in the last 7 days, so newer shows can climb without a large lifetime score. New lists shows by the date they were added, newest first. Charts are recalculated on every boost and every new listing.</p><h2>Featured &amp; promoted</h2><p>Creators can request a promoted spot. Promoted shows appear in a clearly labeled strip and never get a higher chart rank for it.</p><h2>Preview notice</h2><p>This is a first preview. Demo listings are fictional; listings are user-submitted and unverified.</p></article>',"About"))
        elif p == "/api/shows": return self.send(json.dumps(ranked()),ctype="application/json")
        else:
            m = re.fullmatch(r"/show/(\d+)",p)
            if m and (h := show(int(m.group(1)))): return self.send(h)
        self.send(page("Not found",'<h1>Not found</h1><p><a href="/">Back home</a></p>'),404)
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
