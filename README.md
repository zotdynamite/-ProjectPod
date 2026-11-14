# Podcast Stream (v1 preview)

A podcast directory: creators post & promote their shows; visitors browse by category, see honest charts by popularity, and boost shows they like.

## Run
Python 3.9+ (3.8 is enough for SQLite only):

    cd podcast-steam
    python3 app.py            # open http://localhost:8000  (PORT=9000 python3 app.py to change)
                              # PODCAST_DB=/tmp/preview.db uses a different SQLite file

With `DATABASE_URL` unset, data stays in `podcast_steam.db` (SQLite) and psycopg is not imported. On startup the app deletes the old fictional demo shows (by title and `example.com` feed) and, once, inserts the 30 real podcasts in `catalog.json`. Shows a listener already submitted are kept, including their real boosts. Delete the file to reset to those 30 shows. The catalog starts at 0 popularity; a tie follows the Apple Podcasts US Top Shows chart from 13 November 2026, and shows that were not on that chart follow in alphabetical order.

Set `DATABASE_URL` to a Postgres connection string and the same queries run against Postgres instead. The app forces `sslmode=require` unless the string already asks for `verify-ca` or `verify-full`. It does not print the URL. Install the driver with `pip install -r requirements.txt` (`psycopg[binary]`). An empty database is seeded once from `catalog.json` plus the two listener shows stored in the committed `podcast_steam.db` (Houston We Have a Podcast and Python Bytes, with their boosts). Starting again does not insert them twice.

## Render and Neon
The live service is [podcast-stream](https://dashboard.render.com/web/srv-dbnma03rjlhs7398a4cg) (`srv-dbnma03rjlhs7398a4cg`), a Free web service. Free instances have an ephemeral disk, so `podcast_steam.db` disappears on redeploy, restart, and after 15 minutes without traffic. Postgres is what keeps boosts, submissions, and the visitor counter.

There is no `render.yaml`. The service already builds with `pip install -r requirements.txt` and starts with `python app.py`, and auto-deploys `main`. Set this before merging so the deploy that contains this code can reach the database:

1. Create a free Neon project and copy the **pooled** connection string (the host contains `-pooler`). Leave `sslmode=require` on it.
2. In the Render service, open **Environment** and add `DATABASE_URL` with that string. Keep the string out of git. Saving redeploys whatever is on `main` at that moment. The current `main` build does not read `DATABASE_URL`; the deploy after this change is merged does.
3. In **Settings**, leave **Build Command** as `pip install -r requirements.txt` and **Start Command** as `python app.py`.

After merge, the first boot creates the tables and seeds the 30 catalog shows and the two listener submissions. Later boosts, submissions, and page views stay in Neon across spin-downs.

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

HTML page views are stored in `page_views` (Postgres when `DATABASE_URL` is set, otherwise `podcast_steam.db`): path, UTC timestamp, referrer with the query string removed, and a visitor hash. The hash is `sha256(daily salt + client IP + user agent)`. The client IP is the first `X-Forwarded-For` value (Render's proxy), or the socket address when that header is missing. The raw IP is not written to the database or the request log. The salt is random, kept only in memory, and replaced each UTC day so older hashes cannot be recalculated.

Static files, `/api/` responses, HEAD and OPTIONS, common crawlers, and Render health checks (a `Render-Health-Check` header or a `Render/` user agent) are not counted. If logging fails, the page still loads.

`/stats` (HTML) and `/api/stats` (JSON) show views and unique visitors for today (UTC), the last 7 days, and all time, plus top pages and top referrers over the last 7 days. A person who returns on another UTC day counts again in the 7-day and all-time uniques, because the salt changed.

The endpoints stay closed unless `STATS_TOKEN` is set to a non-empty value. Send it as the HTTP Basic password (any username) or as `Authorization: Bearer <token>`. A token in the query string is ignored and is not written to the access log. The comparison is constant-time. On Render, add `STATS_TOKEN` in the service's Environment settings. This repo does not contain the value. With `DATABASE_URL` set, the counts live in Postgres with the rest of the data. With it unset, they live in the SQLite file and disappear with that file.

## Rules
Rank 1 = highest popularity; computed live, so it updates on every boost or new listing. Featured/promotion-requested shows appear in a labeled strip and never affect rank. No payments.
