# Waveline — UX Audit & Redesign Direction

_Author: product design / UX research / full-stack pass. Branch: `ux-navigation-redesign`._

## 1. Problem statement

Waveline's underlying functionality is strong (multi-source artist data, an AI insight
pipeline, discovery, comparison, taste profiles, news, and a small community). The current
site is **too dense** and gives no clear first action: the homepage dumps a long ranked
artist list, full AI articles, and small usage counters above the fold, so a first-time
visitor cannot tell within five seconds what Waveline is _for_ or what to do next.

**Redesign goals**

1. Make the purpose understandable in ~5 seconds.
2. Give every visitor one obvious next action (search an artist).
3. Reorganise around a clear hierarchy: **Discover → Community → News → Compare → Taste Profile → Account**.
4. Consistent header, terminology ("Discover", not Home/Dashboard), and card system on every page.
5. Progressive disclosure: summaries on the homepage, full detail on dedicated pages.
6. No fabricated data, no inflated claims, WCAG 2.2 AA where reasonable.

## 2. Competitor research

Studied ~13 products across four categories: personal music stats, professional artist
analytics, similar-artist discovery, and music community/database. Column key:
J = primary journey, Intro = how the product is introduced, Nav, Search, Hierarchy,
Cards, Empty/Load/Error states, Mobile, Community, Progressive disclosure.

| Product | Category | Primary journey | Intro / first action | Navigation | Search | Notable patterns to borrow | Do NOT copy |
|---|---|---|---|---|---|---|---|
| **Last.fm** | Stats + community | Scrobble → profile → explore | Explains scrobbling; clear "who's listening" | Top bar: Home/Live/Music/Charts/Events + search | Prominent top-bar search | Artist page hierarchy (bio → stats → top tracks → similar → tags); tags as discovery | Cluttered legacy density; ad-heavy pages |
| **stats.fm** | Personal stats | Connect → see your top artists/tracks | Connect account = single clear CTA | Mobile tabs (Home/Search/Profile) | Dedicated search tab | Time-range chips; clean stat cards; compare-with-friends | "Scattered/buggy" feel per reviews; over-gating behind Plus |
| **volt.fm** | Personal stats / artist pages | View shareable music profile | Editorial, share-first profile | Light top nav | Secondary | Editorial typography, shareable profile cards | Pro-only walls on core value |
| **TasteDive** | Similar discovery | Enter a thing → get similar | One input, immediate results | Minimal | Search **is** the homepage | Search-first hero; "because you liked X" reasoning | Sparse styling, dated cards |
| **Music-Map (Gnod)** | Similar discovery | Enter artist → spatial map | Single input, instant map | Almost none | Search is the page | Distance = similarity; click-to-expand exploration | Bare visual design; no hierarchy/mobile care |
| **Chosic** | Discovery utilities | Pick a tool → run it | Tool cards on landing | Utility grid | Per-tool search | **Pathway cards** for each tool (great model for our "product pathways") | Ad density |
| **Viberate** | Pro analytics | Search artist → dashboard | Balanced, "ease of use" praised | Left rail + top | Global search | Readable dashboard, comprehensible cards on a budget | Enterprise breadth we don't need |
| **Chartmetric** | Pro analytics / A&R | Search → deep historical dashboards | A&R/discovery framing | Dense left nav | Global search | Strong trend visualisation; "audience overlap" concept for Compare | Extreme density; too many metrics |
| **Soundcharts** | Pro monitoring | Watch artists you follow | Monitoring framing | Left nav | Global search | Location/audience breakdowns | "Less intuitive" interface; monitoring focus |
| **Songstats** | Pro alerts | Real-time alerts, mobile-first | Mobile-first tracking | Bottom/mobile nav | Global search | Mobile-first navigation, alert cards | Alert model not our use case |
| **Rate Your Music** | Community/database | Rate → lists → discover | Community identity | Text top nav | Prominent search | Rich community + tags; genre trees | Very text-dense, retro UI |
| **Discogs** | Database/marketplace | Search release → contribute | Database + marketplace | Top nav + search | Central search | Contribution/empty-state prompts | Marketplace complexity |
| **Spotify (discovery)** | Streaming discovery | Play → recommendations | "Made for you" rows | Left rail + bottom (mobile) | Top search | Row/carousel discovery, "because you listened" reasons | **Do not clone the Spotify UI**; avoid dark-green clone look |

**Sources:** stats.fm ([site](https://stats.fm/), [App Store](https://apps.apple.com/us/app/stats-fm-for-spotify-music-app/id1526912392)), [volt.fm Super Stats](https://volt.fm/blog/super-stats), analytics comparisons ([Viberate](https://www.viberate.com/blog/music-analytics/top-5-music-analytics-tools-2025/), [Orphiq](https://orphiq.com/resources/music-analytics-platforms-compared)), discovery ([Music‑Map/Chosic](https://www.chosic.com/music-artists-map/), [TasteDive](https://tiorai.com/tools/tastedive/)), empty-state/onboarding ([Mobbin](https://mobbin.com/glossary/empty-state), [Appcues](https://www.appcues.com/blog/in-app-onboarding)).

## 3. Patterns worth adopting (and why)

- **Search-first hero (TasteDive, Music‑Map).** The single strongest lever for the 5‑second
  test. One headline, one supporting line, one big artist search, a few example artists.
- **Pathway cards (Chosic).** Right after the hero, 3–4 cards naming the journeys
  (Analyse an artist / Discover similar / Compare two artists / Build a taste profile /
  Join the community). Verb-led, concise.
- **Artist-page hierarchy (Last.fm).** Bio/identity → key stats → top tracks → similar →
  tags. We already do most of this; keep it, tighten the card system.
- **"Because you liked X" reasoning (Spotify/TasteDive).** Attach a real reason to each
  recommendation _only when data supports it_ (similar-to seed, shared tags). Never invent.
- **Progressive disclosure (all pro tools).** Homepage shows a 2–4 sentence summary +
  "Read full insight"; the full AI article lives on the artist/insight page.
- **Onboarding = the empty state (Mobbin/Slack).** Treat every empty/low-activity screen as
  onboarding: explain the value, show a next action, suggest real seeds — never fake posts.
- **Mobile navigation (Songstats/Spotify).** Compact hamburger or bottom nav; search always
  reachable. No important nav hidden behind unlabelled icons.

## 4. Anti-patterns to avoid (from the brief + research)

- Cloning Spotify's interface or a generic corporate dashboard.
- Inflated claims ("revolutionary", "world-leading", "ultimate").
- Tiny grey text, excessive pills/gradients/glassmorphism, constant animation, neon.
- Dumping the full AI article in every card; dumping an unstructured data blob on Compare.
- Making small usage counters (total searches / today) the dominant social proof.
- Fabricated users, popularity numbers, or community engagement.
- Clickable `<div>`s where buttons/links belong; nav hidden behind unexplained icons.

## 5. Recommended design decisions for Waveline

### Information architecture
Public nav order, identical on every page: **Discover · Community · News · Compare · Taste Profile**.
Right side of header: logged-out → **Log in / Sign up**; logged-in → **notifications bell + profile menu**.
The homepage **is** Discover; retire "Dashboard"/"Home" wording. Show an active-page state.

### Homepage (Discover), top to bottom
1. **Header** (shared, responsive, theme toggle, auth/profile).
2. **Search-first hero** — headline "Discover what makes an artist stand out.", one
   supporting sentence, big search, one primary button, example-artist chips, a one-line
   "what you get".
3. **Product pathways** — 3–4 compact cards (Analyse / Discover similar / Compare / Taste profile / Community).
4. **Recently analysed** — ≤6 compact rows/cards (image, name, one meaningful stat, date,
   View), de-duplicated to the newest snapshot per artist; optional "View all analyses".
5. **Latest insights** — ≤3 compact cards (artist, date, top tracks, 2–4 sentence summary,
   similar preview, "Read full insight"); full article on the artist page.
6. **Discovery recommendations** — 4–6 recommended artists with a real reason each.
7. **Restrained CTA + useful footer** (About, data-source attribution, Privacy, Terms,
   GitHub, creator credit).

Usage counters (28 searches / 20 artists / 1 today) are **too small to be social proof**;
present them subtly (a single quiet "system status" line) rather than as hero stat blocks.

### Community
Logged-out: explain the value + show public Discover content + clear Log in / Create account.
Empty/low-activity: polished empty state, suggest real artists to explore, explain how to make
the first post. Following/Discover tabs clearly labelled and keyboard-accessible. Consistent
post card (user, timestamp, artist context, content, like/comment counts, actions).

### News
Separate **Headlines / New releases / Sources / Refresh status** into clear sections. On a
provider failure: keep loaded sections visible, show a calm inline fallback + accessible
retry, use cached data when present, log the technical error server-side, never surface raw
errors or secrets. Investigate the Spotify new-releases outage (token/endpoint/rate limit).

### Compare
Pre-submit: concise explainer, two labelled inputs, example pairs, the categories that will
be compared, a light empty-state visual. Post-submit: grouped categories (Popularity, Top
tracks, Listening activity, Genre/tags, Similar artists, AI summary) — only categories with
real data. Provide loading / error / no-result states.

### Taste Profile
Logged-out: explain what it is, how it's built, what data is used, why an account helps.
Logged-in: meaningful sections (most-explored artists, favourite tags, discovery patterns,
mainstream-vs-emerging, recent activity, suggestions) — only where real data exists. Do not
infer sensitive personal attributes.

### Auth
Shared identity, visible labels, password requirements, helpful/accessible validation,
autocomplete attributes, focus styles, disabled/loading submit, no account enumeration.

### Visual system & tokens
Contemporary music-editorial feel (keep Waveline's warm terminal/orange identity, not a
green Spotify clone). One token set: colours, typography, spacing scale, radius, shadows,
borders, motion durations, breakpoints (375/390/768/1024/1440). Dark + light mode. Reuse
tokens across a single shared card system. Restrained motion; respect `prefers-reduced-motion`.

### Accessibility & responsive
Semantic landmarks/headings, real buttons/links, labelled forms, visible focus, AA contrast,
touch targets ≥44px, dialog focus management + Escape, alt text, no horizontal overflow at any
tested width, long artist/track/AI text wraps safely.

## 6. Implementation plan (commit groups)

1. UX audit + design foundation (this file + design tokens + shared base layout).
2. Consistent navigation (one header/nav/footer, active state, mobile nav).
3. Homepage information hierarchy (hero, pathways, recently analysed dedup, subtle stats).
4. Insight-card redesign (summary + read-full on dedicated page).
5. Community + empty states.
6. News + API-failure handling.
7. Compare + Taste Profile improvements.
8. Accessibility + responsive fixes.
9. Tests + documentation (IMPLEMENTATION_SUMMARY.md, TEST_REPORT.md).

Constraints honoured throughout: no schema/API/auth changes unless necessary, no secrets in
code, no fake data, no pushes to `main`, no deploy without approval.
