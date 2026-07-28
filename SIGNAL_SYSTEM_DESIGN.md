# Waveline Signal System — design document

This is the design system implemented on the `waveline-signal-system` branch. It
replaces the previous light, card-grid dashboard aesthetic with a darker,
editorial "signal" identity, while preserving the existing Flask backend,
database, auth and APIs exactly as they were.

## Concept

Waveline is framed as a **living music-data signal** travelling through
sources, artists, listeners and communities. Every recurring visual motif
traces back to that idea:

- The hero canvas draws flowing waveform lines with pulsing nodes — audio
  waveform + data pipeline + connected-artist-nodes in one motif.
- Numbered editorial chapters (`01–04`) on the homepage read as stages of a
  signal moving from search → analysis → connections → community.
- Thin "coordinate" labels (`.wv-coord`) and a monospace display face
  (Space Mono) recur throughout to keep the system feeling technical and
  data-native rather than decorative.
- The artist page's accent colour is *derived from the artist's own image*
  client-side — the product visually "tunes in" to each artist.

## Visual identity

| Token | Value | Notes |
|---|---|---|
| `--wv-bg` | `#0a0b0d` | Near-black, dark-first default |
| `--wv-ink` | `#f3eee6` | Warm off-white (not pure white) |
| `--wv-accent` | `#ff5f3d` | The one "signal" accent — warm coral-orange |
| `--wv-on-accent` | `#160f0a` | Dark text on accent surfaces (see contrast note below) |

`body.light` is the alternate theme (warm paper background, deep ink text),
toggled via the theme button; the dark signal theme is the default experience
a first-time visitor sees, matching the brief's "near-black background, warm
off-white typography" direction.

**Contrast was verified, not guessed.** Before committing to the palette I
computed WCAG relative-luminance contrast ratios for every foreground/background
pairing actually used in the UI (off-white ink on near-black: 17:1; muted text:
6.05:1; accent-ink links: 8.52:1; dark text on the accent button: 6.28:1). White
text on the accent button only reached 3.02:1, which fails AA for normal-size
text, so button labels use `--wv-on-accent` (near-black) instead of white.

Explicitly avoided per the brief: purple-to-blue gradients, glassmorphism,
neon-everywhere, identical repeated cards, emoji as feature icons, stock
photography. The dock nav's blur is the one deliberate glass moment, used
once, for a functional reason (it needs to stay legible over any hero content
scrolling beneath it).

Full token set — colour, type, spacing, radius, shadow, motion, z-index,
breakpoints — lives at the top of `static/css/waveline.css`.

## Navigation system

**Desktop**: a slim floating "dock" (`.wv-dock`) rather than a full-width
bar — pill-shaped, blurred, anchored just under the viewport top. Every item
(Discover, Community, Compare, News, Taste) is a text label, never an
icon-only control; the active route gets a filled pill, not just a colour
change, so it reads clearly even to a first-time visitor.

**Mobile**: a sticky bottom bar with four labelled primary destinations
(Discover, Community, Compare, Taste) plus a labelled "More" control that
opens a bottom sheet containing News, About, account/auth actions and the
theme toggle — never a single hamburger hiding the whole product. A search
shortcut lives in the header on every viewport, not just desktop.

**Command palette** (`Cmd/Ctrl+K`, or the Search control): a real search
surface, not a decorative one. It fetches the live `/api/artists` list,
fuzzy-matches as you type, and either navigates straight to an existing
artist's `/artist/<name>` page or — if there's no match — offers "Analyse
this as a new artist," which submits the same `/search` POST the homepage
form uses. Static destinations (Discover, Community, Compare, News, Taste,
About, and Log in / My profile depending on auth state) are always listed.
Full keyboard support: arrow keys to move, Enter to activate, Escape to
close, focus returns to the control that opened it.

## The hero scene

`static/js/signal-scene.js` is a **hand-rolled WebGL scene — no Three.js, no
GSAP, no new dependency.** Rationale: the repository has zero JS
dependencies today (no `package.json`, no bundler, everything is inline
`<script>` in templates), and the brief explicitly says to add dependencies
"only when justified" and to prefer procedural geometry/shaders over
downloaded assets. A fullscreen-triangle vertex shader plus a compact
fragment shader (flowing sine "signal" lines + pulsing lattice nodes,
reacting to pointer position, scroll progress and search-field focus) gets
the same visual outcome as a Three.js particle scene at a fraction of the
payload (roughly 6&nbsp;KB of hand-written JS vs. Three.js's ~150&nbsp;KB+
minified), with no external CDN dependency at all.

Progressive enhancement, in order:
1. A CSS-only radial-gradient fallback (`.wv-hero-scene-fallback`) is always
   present underneath the canvas.
2. If `prefers-reduced-motion: reduce` is set, the script returns immediately
   — the CSS fallback is the entire experience, no canvas is created.
3. If `getContext("webgl")` fails for any reason (old browser, disabled
   GPU, blocked context), the script returns after the `try/catch` — same
   CSS fallback, silently, with no console error.
4. Once a WebGL context, shader compile and program link all succeed, the
   canvas fades in over the CSS fallback (`opacity` transition on
   `.is-ready`).

Runtime behaviour: pauses via `cancelAnimationFrame` on
`visibilitychange` (hidden tab) and via `IntersectionObserver` once the hero
scrolls out of view; skips pointer-following work entirely on
`(pointer: coarse)` devices and lowers line count / device-pixel-ratio cap
for narrow viewports; the canvas has `pointer-events: none` so it can never
intercept clicks, typing or navigation.

**Known limitation**: "reacts to page transitions" from the brief isn't
implemented as a cross-page animation. Waveline is a server-rendered,
multi-page Flask app (by design — a SPA rewrite was explicitly out of
scope), so there is no client-side router to hook a transition into without
a much larger architectural change. The scene does react to scroll, pointer
and search-field focus, which covers the in-page motion requirements.

## Persistent mini-player

Previously duplicated near-verbatim in three templates (`index.html`,
`artist_profile.html`, `compare.html`), each with slightly different
markup/JS. It now lives once in `base.html`, driven by `static/js/player.js`,
exposing a single global `wvPlay(artist, track, triggerEl)` used from every
template's track buttons. Sits above the mobile bottom nav
(`bottom: calc(var(--wv-bottomnav-h) + 12px)`), has play/pause, a progress
bar, source attribution ("30s preview via Deezer"), a close button, and an
`aria-live` region that announces what's now playing.

## Artist page ambient colour

`artist_profile.html` samples the artist's image on a small offscreen canvas
client-side (`getImageData` averaged over a 24×24 downscale) and sets
`--wv-amb` / `--wv-amb-ink` / `--wv-amb-soft` on both the hero and
`<html>`, so the sticky section nav's active pill picks up the same hue.
Wrapped in `try/catch`: Deezer/Spotify images aren't guaranteed to send
permissive CORS headers, so a tainted-canvas `SecurityError` is expected and
silently falls back to the default signal accent — never a visible failure.

## What's deliberately unchanged

Per the brief's "preserve the Flask backend" instruction: no framework
rewrite, no database schema changes, no route removals, no change to how
auth/sessions/Flask-Login work. The one schema-adjacent change is additive
only — none was needed. The one Python-level addition beyond the redesign
itself is `main.about` (`/about`, "How Waveline works") and passing a
`community_posts` preview into the existing `main.dashboard` view.
