# Podcast Stream (v1 preview)

A podcast directory: creators post & promote their shows; visitors browse by category, see honest charts by popularity, and boost shows they like.

## Run
Requires only Python 3.8+ (standard library, no installs, no API keys):

    cd podcast-steam
    python3 app.py            # open http://localhost:8000  (PORT=9000 python3 app.py to change)

Data persists in `podcast_steam.db` (SQLite). On startup the app deletes the old fictional demo shows (by title and `example.com` feed) and, once, inserts the 30 real podcasts in `catalog.json`. Shows a listener already submitted are kept, including their real boosts. Delete the file to reset to those 30 shows. The catalog starts at 0 popularity; a tie follows the Apple Podcasts US Top Shows chart from 13 November 2026, and shows that were not on that chart follow in alphabetical order.

Render deploys this repo from `main` with start command `python app.py` (there is no `render.yaml`). That command runs the startup migration, so the next deploy updates a database that still contains the demo rows. The current service has no persistent disk; a fresh filesystem uses the committed database, and the same migration rewrites an older file if one is still present.

## Pages
- `/` Home – pitch, search, Featured & promoted strip, Top ranked list, categories
- `/?q=term` Search by name/description
- `/browse?category=…` Browse/filter by 19 standard podcast categories
- `/charts?category=…` Overall and per-category charts (rank, show, category, popularity)
- `/submit` Post & promote form (validation; optional RSS fetch fills cover/website if blank)
- `/show/<id>` Show page with ranks, popularity, RSS/website, Listen, Boost button
- `/about`, `/api/shows` (JSON)

## Rules
Rank 1 = highest popularity; computed live, so it updates on every boost or new listing. Featured/promotion-requested shows appear in a labeled strip and never affect rank. No payments.
