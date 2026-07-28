# Waveline Signal System — Test Report

Branch: `waveline-signal-system`. This report only claims what was actually
run, in this environment, on 2026-07-28. No screenshots or live-browser
testing are included — everything below is either an automated test, a
`curl`-against-a-locally-running-instance check, or a static code
inspection; where something wasn't checked, it's called out explicitly
in §6.

## Environment

Unlike a fully sandboxed check, this environment has a **real local
PostgreSQL database** (`music_insights`, 22 existing `searches` rows, 0
users) and a real `.env` with working `SPOTIFY_CLIENT_ID` /
`SPOTIFY_CLIENT_SECRET` / `LASTFM_API_KEY` / `ANTHROPIC_API_KEY`. So in
addition to the mocked pytest suite, routes were also smoke-tested against
the **real** local database and **real** external APIs by actually running
`python3 dashboard.py` and issuing `curl` requests — not just the Flask test
client with everything mocked.

This machine's Python 3.9 (venv, built against LibreSSL rather than
OpenSSL 1.1+) lacks `hashlib.scrypt`, which Werkzeug's password hashing
depends on. This is a pre-existing local-environment limitation (present
before this branch, unrelated to the redesign) and is not expected on
Railway's production Python. It causes 6 pre-existing auth-test failures,
unchanged before and after this work.

## 1. Automated test suite

```
pytest -q
```

- **Baseline (this branch's starting point, before any redesign changes):
  32 passed, 6 failed** (all 6 failures are the pre-existing `hashlib.scrypt`
  environment issue above — confirmed by running the suite before making any
  changes).
- **After all changes: 42 passed, 6 failed** — same 6 pre-existing failures,
  **zero regressions**, **10 new tests added** (`tests/test_signal_system.py`).
- 2 assertions in `tests/test_pages.py` updated for the intentionally
  changed homepage copy/structure ("Find the signal…" hero, "Explore full
  analysis" instead of "Read full insight"), plus `/about` added to the
  route-registration check.

New tests added (`tests/test_signal_system.py`), all passing:

| Test | Verifies |
|---|---|
| `test_desktop_nav_has_labelled_links` | Every primary nav item has a visible text label, not just an icon. |
| `test_mobile_bottom_nav_present` | Bottom nav renders Discover/Community/Compare/Taste + labelled More, plus a mobile search shortcut. |
| `test_mobile_more_panel_has_secondary_actions` | More panel contains News and About links. |
| `test_command_palette_markup_present_and_hidden` | Palette dialog exists, starts `hidden`, is a proper `aria-modal` dialog. |
| `test_mini_player_appears_once_globally` | Exactly one `#wv-player`/`#wv-audio` in the homepage response (was 1 of 3 duplicated implementations before). |
| `test_artist_page_does_not_duplicate_player` | Same, on the artist route's error path (still extends the shared shell). |
| `test_artist_error_uses_shared_shell` | Artist DB-error path now renders the shared nav/footer shell, not a bespoke inline HTML document. |
| `test_about_page_renders` | `/about` returns 200 with expected content (PostgreSQL, Claude mentioned). |
| `test_about_linked_from_footer_and_more_panel` | `/about` is actually reachable from the UI, not an orphan route. |
| `test_new_static_js_assets_are_served` | All 4 new JS files are served with 200 by Flask's static handler. |

## 2. Route checks against a real running instance

Started `PORT=8990 python3 dashboard.py` against the real local `music_insights`
DB (`DATABASE_URL` unset → local fallback) with real API credentials, then
issued real HTTP requests:

| Route | Result |
|---|---|
| `/` | 200, 34.2 KB, no traceback in body |
| `/compare` | 200, 15.4 KB |
| `/compare?a=Nujabes&b=BlakeNor` (real comparison, real Claude call) | 200, 19.2 KB, no traceback |
| `/news` | 200, 17.7 KB (live RSS + Spotify new-releases fetch) |
| `/profile` | 200, 11.8 KB |
| `/feed` | 200, 16.0 KB |
| `/login`, `/register` | 200 |
| `/about` | 200, 14.0 KB |
| `/notifications` (logged out) | 302 → `/login`, correct redirect |
| `/nonexistent-route` | 404 |
| `/artist/Nujabes` (real artist already in DB, real Spotify/Last.fm/Deezer calls) | 200, 24.8 KB; body contains exactly one `id="wv-player"`, the sticky subnav (`wv-subnav` ×2, header nav + rendered links), `data-wv-scrollspy`; **zero** leftover legacy `dv-*` classes |
| `/static/js/nav.js`, `command-palette.js`, `player.js`, `signal-scene.js` | all 200 |
| `/static/css/waveline.css` | 200 |

All checked with `grep -c "Traceback\|werkzeug.exceptions\|jinja2.exceptions"`
against the response body — zero matches on every route above, i.e. no
Flask/Jinja error rendered as a "successful" 200.

Server process log was also inspected directly for unhandled exceptions
during these requests: none.

## 3. JavaScript sanity

`node --check` (syntax-only parse, Node v24) against all 4 new files —
`nav.js`, `command-palette.js`, `player.js`, `signal-scene.js`: **all OK**,
no syntax errors. (This confirms the files parse; it does not substitute
for a live browser console check, which wasn't available in this
environment — see §6.)

## 4. Colour contrast (WCAG relative luminance, computed directly)

Computed contrast ratios for every foreground/background pairing actually
used by the new palette, before committing to the colours:

| Pairing | Ratio | AA (normal text, 4.5:1) |
|---|---|---|
| Off-white ink on near-black bg | 17.05:1 | Pass |
| Muted text on near-black bg | 6.05:1 | Pass |
| Secondary ink on near-black bg | 11.34:1 | Pass |
| Accent-ink links on near-black bg | 8.52:1 | Pass |
| Dark text on accent-coloured buttons | 6.28:1 | Pass |
| White text on accent-coloured buttons | 3.02:1 | **Fail** — not used; buttons use dark text instead |
| Accent text on surface | 6.00:1 | Pass |

## 5. Accessibility — verified in markup / code, not via a live audit tool

Present and checked directly in rendered HTML/CSS/JS: skip link; semantic
`header`/`nav`/`main`/footer` landmarks; one `<h1>` per page with `<h2>`
section headings; real `<button>`/`<a>` elements (no click-`div`s); visible
`:focus-visible` outline on all interactive elements; `aria-current="page"`
on active nav items (desktop dock, mobile bottom nav, mobile sheet);
`aria-modal="true"` + focus trap + Escape-to-close + focus restore on both
the command palette and the mobile More panel; `aria-expanded`/
`aria-haspopup`/`aria-controls` on trigger buttons; `aria-live="polite"`
region announcing mini-player track changes; labelled form inputs
(`<label>`/`.wv-sr-only`); ≥44px touch targets on primary controls;
`prefers-reduced-motion` handled both in CSS (global transition/animation
kill-switch) and in JS (the hero WebGL scene doesn't initialise at all under
reduced motion; scroll-reveal shows content immediately instead of
animating in). 31 `aria-*` attributes counted on the artist page alone.

**Not run**: an automated tool (axe, Lighthouse) or a live screen reader —
this environment has no browser. What's listed above is direct inspection
of the rendered markup, not a substitute for a live audit.

## 6. Not tested (needs a real browser — not available in this environment)

- Live rendering at 375×812 / 390×844 / 768×1024 / 1024×768 / 1440×900 /
  1920×1080 in an actual browser. Layouts are responsive by construction
  (fluid type via `clamp()`, CSS Grid that collapses at defined
  breakpoints, no fixed pixel widths on containers, horizontal-scroll
  containers for wide content) and were reviewed against each breakpoint in
  the CSS source, but **not visually confirmed** in a live viewport.
- Live keyboard-only navigation and screen-reader pass (VoiceOver/NVDA).
- The WebGL hero scene actually rendering in a browser (shader compiles
  were not exercised — no GPU/browser context in this environment). The
  fallback chain (CSS gradient underneath, `try/catch` around every WebGL
  call, silent bail-out) was code-reviewed but not visually confirmed.
- Real mobile-device touch behaviour (bottom nav, More sheet, magnetic
  buttons correctly disabled on touch).
- Lighthouse/performance numbers.
- Cross-browser testing (Safari/Firefox/Chrome differences in `backdrop-filter`,
  `color-mix()`, WebGL support).

## 7. Security review

- No secrets added; `.env` is not tracked; nothing in the diff references
  credential values.
- No database schema changes, no route removed, no existing route's
  URL/method/behaviour changed except the two intentionally documented
  copy changes on `/`.
- Exception/provider detail is not surfaced to users anywhere in the new or
  changed error paths (`error.html` shows a generic message; the one place
  that previously leaked `str(e)` — the artist auto-fetch failure path — now
  doesn't).
- External links continue to use `target="_blank" rel="noopener"`.
- The one new backend data path (`community_posts` on `/`) reuses the
  existing parameterised `social.get_feed` — no new SQL, no new injection
  surface — and is wrapped in `try/except` so a DB error degrades to an
  empty list rather than a 500.
