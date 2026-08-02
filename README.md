# Waveline — Music Discovery Platform

A full-stack music data engineering project built with Python, Flask, and PostgreSQL. Waveline pulls real-time data from Last.fm, Spotify, and Deezer to generate artist insights, track previews, and music discovery features — all via a web dashboard with a custom NTS/Bandcamp-inspired design.

## Features

- **User accounts & profiles** — sign up / log in (Flask-Login, hashed passwords); every user gets a public profile at `/u/<username>` built from the artists they've searched, plus an editable bio
- **Community feed** — post music thoughts and discoveries (optionally tagged with an artist), follow other listeners, and like/comment; a paginated Following / Discover feed at `/feed`, with posts also shown on each profile and relative timestamps ("2h ago")
- **Notifications** — a navbar bell with an unread count and a `/notifications` page when someone likes, comments on, or follows you
- **Richer profiles** — optional location, website/social link, and favourite genres alongside the bio
- **Artist search** — fetches top tracks from Last.fm, generates written insights via the Anthropic Claude API, stores results in PostgreSQL
- **Artist profiles** — dedicated pages per artist with Spotify photo, Last.fm listener counts, Deezer fan count, Spotify popularity, genre tags, top track previews, and similar artists
- **Inline track previews** — 30-second Deezer audio previews via a floating mini player
- **Artist comparison** — side-by-side head-to-head with AI-generated sound analysis
- **Taste profile** — Claude analyses all your searched artists and writes a personal taste profile
- **Music news** — live RSS feeds from Pitchfork, NME, The Guardian, and Resident Advisor
- **New releases** — latest albums and singles pulled from Spotify's new releases endpoint
- **Discovery section** — similar artists to what you've searched, powered by Last.fm
- **Search autocomplete** — dropdown suggestions from your search history
- **Dark mode** — persistent via localStorage, toggle in the topbar
- **Weekly email digest** — automated HTML email summarising the week's artists (Gmail SMTP)
- **Automated scheduling** — APScheduler runs the pipeline daily at 9am

## Tech Stack

| Technology | Purpose |
|------------|---------|
| Python / Flask | Web framework and backend |
| PostgreSQL + JSONB | Data storage |
| Last.fm API | Top tracks, similar artists, listener stats |
| Spotify API | Artist images, genres, popularity, new releases |
| Deezer API | 30-sec track previews, artist photos |
| Anthropic Claude API | AI-generated insights and profiles |
| APScheduler | Automated daily pipeline |
| psycopg2 | PostgreSQL connector |
| python-dotenv | Environment management |

## Project Structure

```
waveline/
├── dashboard.py      # App entry point — config, login, filters, blueprint wiring (gunicorn dashboard:app)
├── services.py       # Shared data clients (Spotify/Last.fm/Deezer/RSS) + Jinja filters
├── views_main.py     # Blueprint — dashboard, search, JSON APIs, preview
├── views_artist.py   # Blueprint — /artist/<name> and /compare
├── views_taste.py    # Blueprint — /profile taste analysis
├── views_news.py     # Blueprint — /news feed
├── templates/        # Jinja page templates (index, artist_profile, taste_profile, compare, news)
├── db.py             # Shared PostgreSQL connection + db_cursor context manager
├── models.py         # User model + schema init (users, posts, follows, likes)
├── auth.py           # Blueprint — register / login / logout (Flask-Login)
├── profiles.py       # Blueprint — public profiles (/u/<username>) + settings + follow
├── social.py         # Data layer for posts, likes, comments, follows, notifications
├── views_feed.py     # Blueprint — community feed, posting, likes, comments
├── views_notifications.py  # Blueprint — notifications page
├── views_admin.py    # Blueprint — owner-only /admin/stats (404 for everyone else)
├── analytics.py       # Self-hosted analytics: after_request recorder + GeoIP + stats queries
├── pipeline.py       # ETL: Last.fm fetch → Claude analysis → PostgreSQL
├── scheduler.py      # APScheduler daily jobs + weekly email digest
├── email_digest.py   # Weekly HTML email builder and sender
├── transform.py      # Analytics report (run directly)
├── genre_trends.py   # Genre trend analysis (run directly)
├── main.py           # CLI entry point for a one-off pipeline run
├── tests/            # pytest suite (helpers, pipeline, db, routes)
├── .github/workflows/ci.yml   # GitHub Actions — runs tests on push/PR
├── requirements.txt  # Pinned runtime dependencies
├── requirements-dev.txt       # Runtime + test dependencies
├── Procfile          # Production start command (gunicorn)
└── .env              # API keys (not committed — see .env.example)
```

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/dimopapa91/music-insight-pipeline.git
cd music-insight-pipeline
```

**2. Create and activate virtual environment**
```bash
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**
```bash
pip install flask requests anthropic psycopg2-binary python-dotenv apscheduler spotipy
```

**4. Set up PostgreSQL**
```bash
createdb music_insights
psql music_insights
```
```sql
CREATE TABLE searches (
    id SERIAL PRIMARY KEY,
    artist_name VARCHAR(255) NOT NULL,
    top_tracks JSONB NOT NULL,
    claude_insight TEXT NOT NULL,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**5. Create your `.env` file**
```
LASTFM_API_KEY=your_lastfm_key
ANTHROPIC_API_KEY=your_anthropic_key
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Optional — for weekly email digest
DIGEST_EMAIL_FROM=your@gmail.com
DIGEST_EMAIL_TO=your@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password

# Optional — self-hosted analytics (see "Analytics" section below)
ADMIN_USERNAME=your_waveline_username
# GEOIP_DB_PATH=/path/to/GeoLite2-Country.mmdb
```

## Usage

**Run the dashboard (local development):**
```bash
python3 dashboard.py
```
Then open `http://localhost:5000` in your browser.

**Run in production (gunicorn):**
```bash
gunicorn dashboard:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```
This is the command in the `Procfile`, used automatically on Railway.

**Run the tests:**
```bash
pip install -r requirements-dev.txt
pytest
```

**Run the scheduled pipeline:**
```bash
python3 scheduler.py
```

**Send the weekly digest manually:**
```bash
python3 email_digest.py
```

## Pages

| Route | Description |
|-------|-------------|
| `/` | Main dashboard — search, stats, recent insights |
| `/register`, `/login`, `/logout` | Account sign-up and session auth |
| `/u/<username>` | Public user profile (searched artists, posts, bio) |
| `/u/<username>/follow` | Follow / unfollow a user (login required) |
| `/settings` | Edit your own profile (login required) |
| `/feed` | Community feed — Following / Discover tabs, paginated |
| `/post` · `/post/<id>/like` · `/post/<id>/comment` | Post, like, comment (login required) |
| `/notifications` | Your likes / comments / follows notifications (login required) |
| `/artist/<name>` | Full artist profile with multi-source data |
| `/compare?a=X&b=Y` | Side-by-side artist comparison |
| `/profile` | Your personal taste profile |
| `/news` | Music news + Spotify new releases |
| `/admin/stats` | Owner-only self-hosted analytics — 404 for everyone except `ADMIN_USERNAME` |

## Analytics

Waveline records a lightweight, **self-hosted** pageview log — no third-party
analytics service and no client-side tracking script. A Flask
`after_request` hook (`analytics.py`) inserts one row per real HTML page
view into an `analytics_events` table; static assets, JSON APIs, the
Deezer preview endpoint, the admin area itself, and non-GET requests are
all skipped.

**Privacy**: no raw IP address is ever stored. Each row's visitor identifier
is `sha256(ip + date + user_agent + SECRET_KEY)`, truncated — a hash that
**rotates every UTC day**, so it's never a durable cross-day identifier and
the raw IP never touches the database.

The aggregated stats — pageviews, unique visitors, top countries/pages/
referrers, signups, searches, community activity — are visible at
`/admin/stats`, which requires being logged in **and** having a username
that matches the `ADMIN_USERNAME` environment variable. Anyone else gets a
plain 404.

### GeoIP setup (optional)

Country breakdowns need a MaxMind **GeoLite2-Country** database:

1. Create a free MaxMind account at [maxmind.com](https://www.maxmind.com/en/geolite2/signup) and download `GeoLite2-Country.mmdb`.
2. Put the file somewhere on the server (not in the repo — it's `.gitignore`d as `*.mmdb`, and MaxMind's license doesn't allow redistributing it anyway).
3. Set `GEOIP_DB_PATH` to its absolute path.
4. On Railway: add a persistent [volume](https://docs.railway.app/reference/volumes), upload the `.mmdb` file into it, and set `GEOIP_DB_PATH` to the mounted path in the service's environment variables.

If `GEOIP_DB_PATH` is unset, or the file can't be read, analytics still work
exactly the same — `country` is just recorded as `NULL` and the "top
countries" table is simply empty.

---

Built by [Dimos Dimitrios Papageorgiou](https://github.com/dimopapa91) — Manchester, UK
