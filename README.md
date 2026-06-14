# 🎵 Music Insight Pipeline

An automated data engineering pipeline that fetches real music data from the Last.fm API, stores it in a PostgreSQL database, and uses the Anthropic Claude AI to generate intelligent music insights — all running on a daily schedule.

## 🚀 What It Does

- Fetches top tracks for artists from the **Last.fm API**
- Analyses the data using the **Anthropic Claude API** to generate music insights
- Stores all results in a **PostgreSQL** database
- Runs **automatically every day at 9am** using APScheduler
- Generates **data reports** showing trends, play counts and pipeline stats

## 🛠️ Tech Stack

| Technology | Purpose |
|------------|---------|
| Python | Core language |
| Last.fm API | Music data source |
| Anthropic Claude API | AI-powered analysis |
| PostgreSQL | Data storage |
| APScheduler | Pipeline scheduling |
| psycopg2 | PostgreSQL connector |
| python-dotenv | Environment management |

## 📁 Project Structure

music-insight-pipeline/
├── pipeline.py      # Core ETL functions (fetch, analyse, store)
├── scheduler.py     # Automated daily scheduling
├── transform.py     # Data transformation and reporting
├── main.py          # Interactive mode (search any artist)
├── pipeline.log     # Auto-generated run logs
└── .env             # API keys (not committed)



## ⚙️ Setup

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
pip install requests anthropic psycopg2-binary python-dotenv apscheduler
```

**4. Set up PostgreSQL**
```bash
createdb music_insights
psql music_insights
```
Then run:
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

LASTFM_API_KEY=your_lastfm_key
LASTFM_SECRET=your_lastfm_secret
ANTHROPIC_API_KEY=your_anthropic_key



## 🎮 Usage

**Interactive mode — search any artist:**
```bash
python3 main.py
```

**Run the scheduled pipeline:**
```bash
python3 scheduler.py
```

**Generate a data report:**
```bash
python3 transform.py
```

## 📊 Example Output


========================================
   MUSIC INSIGHT PIPELINE — REPORT
========================================
📈 Pipeline Stats:
  Total searches run: 4
  Unique artists analysed: 4

📊 Average Track Plays by Artist:
  Billie Eilish: 32,953,883 avg plays
  Nujabes: 3,860,108 avg plays

## 🔮 Planned Features

- [ ] Web dashboard to visualise trends
- [ ] Multi-source pipeline (Spotify + Last.fm)
- [ ] Artist comparison mode
- [ ] Genre trend analysis

---

Built by [Dimos Dimitrios Papageorgiou](https://github.com/dimopapa91) — Manchester, UK