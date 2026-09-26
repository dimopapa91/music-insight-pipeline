"""Icon set (2026 redesign): one SVG sprite, no emoji/text-glyph icons left
in the interface, and the player never flattens a button's markup."""

import glob
import re


def _read(path):
    with open(path) as f:
        return f.read()


def test_sprite_defines_every_icon_the_templates_use():
    sprite = _read("templates/_icons.html")
    defined = set(re.findall(r'<symbol id="wv-i-([a-z]+)"', sprite))
    used = set()
    for path in glob.glob("templates/*.html"):
        used |= set(re.findall(r'icon\("([a-z]+)"\)', _read(path)))
    used |= set(re.findall(r'iconSvg\(playing \? "([a-z]+)" : "([a-z]+)"\)', _read("static/js/player.js"))[0])
    assert used and used <= defined, used - defined


def test_no_emoji_or_glyph_icons_left_in_templates_or_player():
    for path in glob.glob("templates/*.html") + ["static/js/player.js"]:
        text = _read(path)
        for glyph in ("🔔", "▶", "⏸", "⏳", "↗"):
            assert glyph not in text, f"{glyph} still in {path}"


def test_templates_using_icons_import_the_macro():
    for path in glob.glob("templates/*.html"):
        text = _read(path)
        if "icon(\"" in text and not path.endswith("_icons.html"):
            assert 'import icon' in text, path


def test_player_marks_loading_with_a_class_not_by_rewriting_text():
    js = _read("static/js/player.js")
    assert "triggerEl.textContent" not in js
    assert 'classList.add("is-loading")' in js and 'classList.remove("is-loading")' in js


def test_base_emits_the_sprite_once():
    assert _read("templates/base.html").count("{{ sprite() }}") == 1
