"""Mobile polish (2026 redesign). Guards the fixes found by auditing every
page at 390px: tap targets, iOS focus-zoom, and the phone layout order.
"""

import re


def _read(path):
    with open(path) as f:
        return f.read()


def _mobile_block(css):
    start = css.index("/* ── Mobile polish (2026 redesign)")
    return css[start:]


def test_inputs_are_16px_on_phones_so_ios_does_not_zoom():
    block = _mobile_block(_read("static/css/waveline.css"))
    assert re.search(r"input:not\(\[type=\"file\"\]\)[^{]*\{\s*font-size:\s*16px", block)


def test_chips_and_small_buttons_are_finger_sized_on_phones():
    block = _mobile_block(_read("static/css/waveline.css"))
    assert re.search(r"\.wv-chip\s*\{\s*min-height:\s*40px", block)
    assert re.search(r"\.wv-btn-sm\s*\{\s*min-height:\s*42px", block)


def test_small_button_text_is_readable():
    css = _read("static/css/waveline.css")
    size = float(re.search(r"\.wv-btn-sm \{[^}]*font-size:\s*([\d.]+)rem", css).group(1))
    assert size >= 0.8          # was .68rem (~10.9px)


def test_pulse_label_stays_for_screen_readers_when_hidden_on_phones():
    base = _read("templates/base.html")
    assert '<span class="wv-pulse-lbl">Just analysed</span>' in base
    block = _mobile_block(_read("static/css/waveline.css"))
    rule = re.search(r"\.wv-pulse-lbl\s*\{([^}]*)\}", block).group(1)
    assert "display: none" not in rule and "clip" in rule   # visually hidden, still announced


def test_homepage_phone_order_is_search_then_photo():
    html = _read("templates/index.html")
    block = html[html.index("Mobile order: search first"):]
    block = block[:block.index("}\n  }") + 4]
    assert ".wv-hero-side { display: contents; }" in block
    orders = {name: int(re.search(rf"\.{name} \{{ order: (\d)", block).group(1))
              for name in ("wv-hero-inner", "wv-feature")}
    assert orders["wv-hero-inner"] < orders["wv-feature"]


def test_textareas_beat_page_level_font_sizes_on_phones():
    # ".cm-compose textarea" (0,1,1) used to beat a bare "html body textarea"
    # (0,0,3), leaving the feed composer at 15.2px (iOS focus zoom).
    block = _mobile_block(_read("static/css/waveline.css"))
    assert "html body textarea:not([hidden])" in block


def test_track_play_buttons_are_finger_sized():
    html = _read("templates/artist_profile.html")
    rule = re.search(r"\.ar-track \.nm \{([^}]*)\}", html).group(1)
    assert "min-height: 44px" in rule
