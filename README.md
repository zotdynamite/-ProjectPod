# Podcast Stream (v1 preview)

A podcast directory: creators post & promote their shows; visitors browse by category, see honest charts by popularity, and boost shows they like.

## Run
Requires only Python 3.8+ (standard library, no installs, no API keys):

    cd podcast-steam
    python3 app.py            # open http://localhost:8000  (PORT=9000 python3 app.py to change)

Data persists in `podcast_steam.db` (SQLite, created and seeded with 12 fictional demo shows on first run). Delete the file to reset.

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
