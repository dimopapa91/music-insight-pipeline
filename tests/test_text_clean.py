"""Tests for text_clean.strip_em_dashes -- the deterministic enforcement
layer behind the "no em dashes" instruction in every Claude prompt in this
codebase. A prompt instruction is a request, not a guarantee, so this is
what actually makes the promise hold."""

from text_clean import strip_em_dashes


def test_em_dash_becomes_comma_with_spacing():
    assert strip_em_dashes("Radiohead—Muse") == "Radiohead, Muse"


def test_em_dash_with_surrounding_spaces():
    assert strip_em_dashes("part one — part two") == "part one, part two"


def test_en_dash_is_also_stripped():
    assert strip_em_dashes("2010–2015") == "2010, 2015"


def test_multiple_dashes_in_one_string():
    text = "moody — atmospheric — textured production"
    assert strip_em_dashes(text) == "moody, atmospheric, textured production"


def test_hyphens_are_left_alone():
    text = "a well-produced, genre-bending record"
    assert strip_em_dashes(text) == text


def test_no_dash_present_is_unchanged():
    text = "Clean prose with no dashes at all."
    assert strip_em_dashes(text) == text


def test_double_spaces_collapsed():
    assert strip_em_dashes("one  —  two") == "one, two"


def test_no_space_before_trailing_punctuation():
    assert strip_em_dashes("great tracks — produced well.") == "great tracks, produced well."


def test_falsy_input_returned_unchanged():
    assert strip_em_dashes("") == ""
    assert strip_em_dashes(None) is None


def test_idempotent_on_already_clean_text():
    text = "Fans of one, will likely enjoy the other, too."
    assert strip_em_dashes(text) == text
