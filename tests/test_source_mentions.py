"""AI copy never names data sources (27 Sep 2026): prompts ask for it,
strip_source_mentions() enforces it on new and stored text."""

import datetime

import pytest

import agent_api
from text_clean import clean_ai_text, strip_source_mentions


@pytest.mark.parametrize("raw,clean", [
    ("J Dilla's most popular tracks on Last.fm reveal an artist whose appeal is wide.",
     "J Dilla's most popular tracks reveal an artist whose appeal is wide."),
    ("According to Last.fm, the fanbase is devoted.", "The fanbase is devoted."),
    ("Teardrop dominates with 46 million Last.fm plays.", "Teardrop dominates with 46 million plays."),
    ("His Spotify popularity score of 82 shows reach.", "His popularity score of 82 shows reach."),
    ("The data from Last.fm and Spotify suggests broad appeal.", "The data suggests broad appeal."),
    ("It is huge (Last.fm) and loved.", "It is huge and loved."),
    ("The track, according to Spotify data, is a classic.", "The track is a classic."),
    ("It is their biggest song according to Last.fm.", "It is their biggest song."),
    ("Last.fm's tags place them in trip-hop. Spotify listeners agree.",
     "Tags place them in trip-hop. Listeners agree."),
    ("The Last.fm data suggests broad appeal.", "The data suggests broad appeal."),
])
def test_source_names_removed_cleanly(raw, clean):
    assert strip_source_mentions(raw) == clean


@pytest.mark.parametrize("text", [
    "Portishead's album Dummy is similar. e.g. this stays as written.",
    "These five tracks reveal that Massive Attack's broad appeal stems from atmosphere.",
    "They sound like Spotify-era pop.",   # a style descriptor, not a data source
])
def test_ordinary_text_untouched(text):
    assert strip_source_mentions(text) == text


def test_clean_ai_text_also_removes_em_dashes():
    assert clean_ai_text("Big on Last.fm — and loved.") == "Big, and loved."


def test_falsy_input_passthrough():
    assert strip_source_mentions("") == "" and strip_source_mentions(None) is None


def test_paid_api_and_mcp_serve_cleaned_text():
    body = agent_api._insight_body(("J Dilla", "His tracks on Last.fm glow.", datetime.datetime(2026, 9, 1)))
    assert body["insight"] == "His tracks glow."


def test_prompts_ask_claude_not_to_name_sources():
    for path in ("pipeline.py", "views_artist.py", "views_taste.py", "compare.py"):
        src = open(path).read()
        assert "do not name any data platform" in src, path


def test_ai_copy_is_cleaned_in_templates_but_user_posts_are_not():
    import glob
    uses = "".join(open(p).read() for p in glob.glob("templates/*.html"))
    assert uses.count("| clean_ai") >= 7
    assert "body | clean_ai" not in uses
