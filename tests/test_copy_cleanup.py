"""Copy cleanup (27 Sep 2026): no data-source names outside the About page
and "Open on Spotify" links, and no em dashes in visible template text."""

import glob
import re

SOURCES = re.compile(r"last\.fm|deezer|musicbrainz|anthropic|\bclaude\b|scrobbles? from|via spotify", re.I)


def _visible(path):
    s = open(path).read()
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"\{%.*?%\}", "", s, flags=re.S)       # jinja statements
    s = re.sub(r"\{\{.*?\}\}", "", s, flags=re.S)     # jinja expressions
    s = re.sub(r"<[^>]+>", " ", s)                      # tags and attributes
    return s


def test_no_source_names_in_visible_text_except_about():
    offenders = {}
    for p in glob.glob("templates/*.html"):
        if p.endswith(("about.html", "admin_stats.html")):
            continue
        hits = SOURCES.findall(_visible(p))
        if hits:
            offenders[p] = hits
    assert not offenders, offenders


def test_player_no_longer_credits_deezer():
    assert "via Deezer" not in open("static/js/player.js").read()


def test_genres_page_is_heading_only():
    s = _visible("templates/genres.html")
    assert "Browse by sound" in s
    assert "Eight genres" not in s and "Explore" not in s


def test_no_em_dashes_in_visible_template_text():
    offenders = {}
    for p in glob.glob("templates/*.html"):
        text = _visible(p)
        if "—" in text or "&mdash;" in text:
            offenders[p] = [ln.strip()[:80] for ln in text.splitlines() if "—" in ln]
    assert not offenders, offenders


def test_no_em_dashes_in_page_titles():
    for p in glob.glob("templates/*.html"):
        for title in re.findall(r"\{% block title %\}(.*?)\{% endblock %\}", open(p).read()):
            assert "—" not in title, (p, title)
