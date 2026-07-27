# Waveline UX Redesign — Test Report

Branch: `ux-navigation-redesign`. This report only claims what was actually run.

## Environment

Work was done in a sandbox **without a live PostgreSQL server or real Spotify/Last.fm/Deezer/Claude
credentials**. Automated tests and route checks therefore use the Flask **test client** with the
database and external APIs **mocked**. Full live-browser, real-API and Lighthouse testing needs the
branch running against a real instance (see "Not tested" below).

## 1. Automated test suite

- **Baseline (before changes): 38 passed.**
- **After all changes: 38 passed.** (`pytest -q`)
- Suites: `test_helpers`, `test_pipeline`, `test_db`, `test_routes`, `test_auth`, `test_feed`,
  `test_polish`, `test_pages`.
- 3 assertions updated for the new markup (homepage hero text; navbar badge class `wv-badge`;
  homepage now asserts "Read full insight" instead of showing the full article).

## 2. Route render checks (Flask test client, mocked data) — PASS

| Route | Checked |
|---|---|
| `/` Discover | hero, search input, pathways, deduped recently-analysed, insight **summaries** (no inline full article), recommendations, active nav, stylesheet served (200). |
| `/news` | live case (headlines/releases/sources) **and** degraded case (articles kept, calm fallback, no raw provider error). |
| `/feed` Community | logged-out explainer + create-account, empty state, and a rendered post with comment/author. |
| `/compare` | pre-submit (explainer, two inputs, categories, examples) and post-submit (per-artist cards, tracks, AI section with calm fallback). |
| `/profile` Taste | logged-out explainer state (what it is / how built / account CTA). |
| `/login`, `/register` | correct labels, `autocomplete` values, password hint, shared nav. |
| `/notifications` | list item + empty state, shared nav. |
| `/artist/<name>` | via the real route with mocked DB/APIs: name, full insight, stats, top tracks, similar, shared nav (HTTP 200). |
| `/u/<username>` | via the real route with mocked User/social funcs: `@username`, location, bio, artist chips, posts/empty state, shared nav (HTTP 200). |
| `/settings` | via the real route with a logged-in mock user: prefilled bio/location/website/genres fields, saved notice, link to public profile, shared nav (HTTP 200). |
| `/static/css/waveline.css` | served (200, `text/css`). |

## 3. State-specific behaviour verified

- **Failed API request:** `/news` with empty releases → articles remain, calm inline fallback + retry, no raw error. Compare/Taste AI failure → calm message, **no provider/exception detail leaked**.
- **Empty database results:** Community empty feed, Taste empty state, "recently analysed"/insights sections hidden when no data.
- **Duplicate artist results:** homepage uses the existing `DISTINCT ON (artist_name)` newest-per-artist query, so each artist appears once (verified by rendering repeated mock rows → deduped output section).
- **Auth flows:** register success/duplicate-username and login success/wrong-password paths pass in `test_auth`.

## 4. Accessibility (implemented; verified in markup, not via a live audit tool)

Implemented: semantic landmarks (`header`/`nav`/`main`/`footer`), skip link, one `<h1>` per page with
`<h2>` sections, real `<a>`/`<button>` (no click-`div`s in new templates), visible `:focus-visible`
styles, `aria-current="page"` on active nav, `aria-live` on auth errors, labelled inputs (`<label>`/
`.wv-sr-only`), ≥44px touch targets on primary controls, `alt=""` on decorative art, `prefers-reduced-
motion` handling, mobile nav button with `aria-expanded`/`aria-controls`.
**Not yet run:** an automated axe/Lighthouse audit against a live instance, and live screen-reader/keyboard testing.

## 5. Security review

- **No secrets** in the branch diff; `.env` is not tracked.
- **No schema, API-contract or auth-behaviour changes** (login redirect allowlist and enumeration behaviour unchanged).
- Provider/exception detail is **not** surfaced to users (news, compare, taste).
- External links use `target="_blank" rel="noopener"`.
- One query added filters by `user_id` using a parameterised query (no injection).

## 6. Not tested (needs a running instance / real APIs)

- Live rendering at 375 / 390 / 768 / 1024 / 1440px in a real browser (layouts are responsive by
  construction — no fixed widths, grids collapse, `overflow-wrap` on long text — but not visually
  confirmed live).
- Real Spotify/Last.fm/Deezer/Claude responses, and the exact Spotify new-releases failure code.
- Lighthouse performance numbers; live keyboard/screen-reader passes.
- **Before/after screenshots:** "before" can be captured from the live production site; "after" needs
  the branch deployed to a preview or run locally against a real DB. None were captured because
  nothing was deployed.
