# Implementation summary — Waveline Signal System

Branch: `waveline-signal-system` (based on the tip of `cleanup-emdash-script`,
which is `origin/main` plus two local unpushed commits — the most current
state of the repo; local `main` was stale and missing two already-merged
PRs, including the previous `ux-navigation-redesign` pass this one builds
on top of). Not merged, not pushed, not deployed.

Baseline before this work: 32 passing / 6 pre-existing environment-only
failures (see `TEST_REPORT.md`). After this work: 42 passing / the same 6
pre-existing failures — 10 new tests added, zero regressions.

## What changed

### New files
- `static/js/nav.js` — theme toggle (dark-first, `body.light` alt theme),
  mobile "More" panel (open/close, focus trap, Escape, scroll lock),
  scroll-reveal for homepage chapters, magnetic-button effect for
  pointer-fine devices, generic scroll-spy helper for sticky in-page nav.
- `static/js/command-palette.js` — global `Cmd/Ctrl+K` command palette wired
  to the real `/api/artists` endpoint and the real `/search` POST flow.
- `static/js/player.js` — the persistent mini-player, consolidating three
  near-duplicate per-template implementations into one.
- `static/js/signal-scene.js` — the hand-rolled WebGL hero scene (see
  `SIGNAL_SYSTEM_DESIGN.md` for the full rationale and fallback chain).
- `templates/about.html` + `main.about` route (`/about`) — the "How Waveline
  works" page: data sources, pipeline, storage, AI, deployment, community,
  plus a "Build with Waveline" link to the portfolio and GitHub.
- `templates/error.html` — one shared error template extending `base.html`,
  replacing three hand-rolled inline `<html>` documents that bypassed the
  whole design system (and, in one case, leaked raw exception text to users).
- `tests/test_signal_system.py` — 10 new tests for the redesigned shell.
- `SIGNAL_SYSTEM_DESIGN.md`, this file, `TEST_REPORT.md`.

### Rewritten
- `static/css/waveline.css` — full token rewrite (dark-first near-black/
  warm-off-white/signal-accent palette, contrast-checked; z-index scale;
  breakpoint tokens; command palette, bottom nav, More panel, mini-player,
  sticky subnav, numbered chapter and scroll-reveal component styles added).
- `templates/base.html` — desktop dock nav, mobile bottom nav + More panel,
  command palette markup, one persistent mini-player instance, updated
  footer (How Waveline works, portfolio, "Interested in the project?").
- `templates/index.html` — full-viewport hero with the signal canvas, real
  search with suggestions/examples/recent-searches (client-side, real, not
  fake), then four numbered editorial chapters (Search the signal / Understand
  the artist / Follow the connections / Join the community) built from
  existing `get_dashboard_data()` output plus a new small `community_posts`
  preview.
- `templates/artist_profile.html` — editorial hero with client-derived
  ambient accent colour, sticky scroll-spy section nav (Overview / Top
  Tracks / AI Insight / Similar Artists / Compare), an explicit "AI-generated
  by Claude" flag on the insight, a "Compare this artist" quick action.
- `templates/compare.html` — animated connector between the two artist
  inputs, symmetric before/after layout, explicit "edit and compare again"
  affordance; per-page player markup removed (now global).

### Backend (minimal, additive only)
- `views_main.py` — added `/about` route; `dashboard()` now also passes a
  small real `community_posts` preview (existing `social.get_feed`, wrapped
  in try/except so a DB hiccup degrades to an empty list, never a 500).
- `views_artist.py` — removed two inline HTML-string error pages in favour of
  `render_template("error.html", ...)`; stopped leaking raw exception text
  (`str(e)`) to users on pipeline failure, matching the "never leak provider
  detail" convention already used in `compare()`.
- `views_taste.py` — same inline-error-page removal.
- `tests/test_pages.py` — two assertions updated to match the intentionally
  changed hero copy/structure ("Find the signal…" / "Explore full analysis"),
  plus `/about` added to the route-registration check.

No other backend module was touched. No database schema changes. No route
removed. No existing route's URL or method changed.

## Dependencies added

**None.** No new Python package, no new JS library, no CDN dependency beyond
the Google Fonts link that was already there. The hero scene is hand-written
WebGL; the command palette, nav and player are hand-written vanilla JS. This
was a deliberate call — see the "hero scene" section of
`SIGNAL_SYSTEM_DESIGN.md` for the reasoning.

## Remaining limitations / what's intentionally not done

- **Community, News, Taste Profile, and account pages (profile, settings,
  notifications)** inherit the full new design system automatically (dock
  nav, bottom nav, command palette, dark signal palette, tokens) since they
  all extend `base.html`, but were not individually rebuilt into fully
  bespoke editorial layouts. Homepage, navigation, and the artist and
  compare pages — the pages the brief calls out explicitly ("shared
  navigation, homepage and at least the main application pages") — received
  the full treatment. Extending the same editorial depth to Community/News/
  Taste is the natural next slice of work.
- **Cross-page transition animation** for the hero scene isn't implemented;
  documented in the design doc — Waveline is intentionally still a
  server-rendered multi-page app, so there's no client router to hook into.
- **Shareable taste-profile visual identity** (a distinct generated graphic)
  wasn't added beyond the existing AI-written taste text and explored-artist
  chips; would need new design work, not just reuse of existing data shapes.
- **`hashlib.scrypt` is unavailable in this machine's Python 3.9** (built
  against LibreSSL, not OpenSSL 1.1+), which pre-dates this branch and fails
  6 auth tests locally regardless of these changes — see `TEST_REPORT.md`.
  Does not affect Railway's production Python.
- The pre-existing `requirements.txt` pins `gunicorn==26.0.0`, which doesn't
  exist on PyPI (latest at time of testing is 23.0.0) — pre-existing, not
  touched by this branch, noted here since it blocked a clean
  `pip install -r requirements-dev.txt` during setup.

## Local commands

```bash
source venv/bin/activate
pytest -q                                   # run the test suite

# macOS: port 5000 is usually taken by AirPlay Receiver — use another port
PORT=8990 python3 dashboard.py              # run the app locally
# then open http://127.0.0.1:8990
```

Uses the existing local Postgres `music_insights` database and the existing
`.env` (`DATABASE_URL` unset locally falls back to that DB; the real
`SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`/`LASTFM_API_KEY`/
`ANTHROPIC_API_KEY` values already in `.env` were used for local QA, exactly
as the app already expected).
