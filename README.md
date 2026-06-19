# Waveline — Music Discovery Platform

A full-stack music data engineering project built with Python, Flask, and PostgreSQL. Waveline pulls real-time data from Last.fm, Spotify, and Deezer to generate artist insights, track previews, and music discovery features — all via a web dashboard with a custom NTS/Bandcamp-inspired design.

## Features

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
├── dashboard.py      # Flask app — all routes and templates
├── pipeline.py       # ETL: Last.fm fetch → Claude analysis → PostgreSQL
├── scheduler.py      # APScheduler daily jobs + weekly email digest
├── email_digest.py   # Weekly HTML email builder and sender
├── pipeline.log      # Auto-generated run logs
└── .env              # API keys (not committed)
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
```

## Usage

**Run the dashboard:**
```bash
python3 dashboard.py
```
Then open `http://localhost:5000` in your browser.

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
| `/artist/<name>` | Full artist profile with multi-source data |
| `/compare?a=X&b=Y` | Side-by-side artist comparison |
| `/profile` | Your personal taste profile |
| `/news` | Music news + Spotify new releases |

---

Built by [Dimos Dimitrios Papageorgiou](https://github.com/dimopapa91) — Manchester, UK
