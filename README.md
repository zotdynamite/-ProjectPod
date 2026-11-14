# Podcast Stream (v1 preview)

A podcast directory: creators post & promote their shows; visitors browse by category, see honest charts by popularity, and boost shows they like.

## Run
Requires only Python 3.8+ (standard library, no installs, no API keys):

    cd podcast-steam
    python3 app.py            # open http://localhost:8000  (PORT=9000 python3 app.py to change)
                              # PODCAST_DB=/tmp/preview.db uses a different SQLite file

Data persists in `podcast_steam.db` (SQLite). On startup the app deletes the old fictional demo shows (by title and `example.com` feed) and, once, inserts the 30 real podcasts in `catalog.json`. Shows a listener already submitted are kept, including their real boosts. Delete the file to reset to those 30 shows. The catalog starts at 0 popularity; a tie follows the Apple Podcasts US Top Shows chart from 13 November 2026, and shows that were not on that chart follow in alphabetical order.

Render deploys this repo from `main` with start command `python app.py` (there is no `render.yaml`). That command runs the startup migration, so the next deploy updates a database that still contains the demo rows. The current service has no persistent disk; a fresh filesystem uses the committed database, and the same migration rewrites an older file if one is still present.

That filesystem is ephemeral. The live service is a Free web service, and Free instances cannot attach a persistent disk. Render drops local files — including `podcast_steam.db` — on redeploy, on restart, and when the free instance spins down after 15 minutes without traffic. Listener submissions, boosts, popularity, and the visitor counter all live in that file, so they reset when it is discarded. A fresh boot starts from the committed database, then startup removes demo rows and seeds the catalog. Keeping the data would mean a paid Render disk, an external free Postgres such as Neon or Supabase, or Render's free Postgres, which expires 30 days after creation. None of those are wired up.

## Pages
- `/` Home – pitch, search, Featured & promoted strip, Top ranked list, categories
- `/?q=term` Search by name/description
- `/browse?category=…` Browse/filter by 19 standard podcast categories
- `/charts?category=…` Overall and per-category charts (rank, show, category, popularity)
- `/submit` Post & promote form (validation; optional RSS fetch fills cover/website if blank)
- `/show/<id>` Show page with ranks, popularity, RSS/website, Listen, Boost button
- `/about`, `/api/shows` (JSON)
- `/stats`, `/api/stats` — visitor counts, closed unless `STATS_TOKEN` is set

## Visitor stats

HTML page views are stored in `page_views` inside `podcast_steam.db`: path, UTC timestamp, referrer with the query string removed, and a visitor hash. The hash is `sha256(daily salt + client IP + user agent)`. The client IP is the first `X-Forwarded-For` value (Render's proxy), or the socket address when that header is missing. The raw IP is not written to the database or the request log. The salt is random, kept only in memory, and replaced each UTC day so older hashes cannot be recalculated.

Static files, `/api/` responses, HEAD and OPTIONS, common crawlers, and Render health checks (a `Render-Health-Check` header or a `Render/` user agent) are not counted. If logging fails, the page still loads.

`/stats` (HTML) and `/api/stats` (JSON) show views and unique visitors for today (UTC), the last 7 days, and all time, plus top pages and top referrers over the last 7 days. A person who returns on another UTC day counts again in the 7-day and all-time uniques, because the salt changed.

The endpoints stay closed unless `STATS_TOKEN` is set to a non-empty value. Send it as the HTTP Basic password (any username) or as `Authorization: Bearer <token>`. A token in the query string is ignored and is not written to the access log. The comparison is constant-time. On Render, add `STATS_TOKEN` in the service's Environment settings. This repo does not contain the value. Counts reset with the rest of the SQLite file; see the note in Run.

## Rules
Rank 1 = highest popularity; computed live, so it updates on every boost or new listing. Featured/promotion-requested shows appear in a labeled strip and never affect rank. No payments.
