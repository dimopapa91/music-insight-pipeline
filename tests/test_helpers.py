"""Tests for pure helper functions (no DB or network needed)."""

import dashboard


def test_artist_titlecase_preserves_stylised_caps():
    assert dashboard.artist_titlecase("blakenor") == "Blakenor"
    # Stylised internal capitals are preserved, not flattened
    assert dashboard.artist_titlecase("BlakeNor") == "BlakeNor"
    assert dashboard.artist_titlecase("tyler the creator") == "Tyler The Creator"
    assert dashboard.artist_titlecase("") == ""


def test_markdown_preview_strips_html_and_truncates():
    text = "# Heading\n\nSome **bold** insight about music."
    preview = dashboard.markdown_preview(text, length=12)
    assert "<" not in preview and ">" not in preview
    assert "#" not in preview
    assert len(preview) <= 12


def test_render_markdown_renders_and_escapes():
    out = str(dashboard.render_markdown("Hello **world**"))
    assert "<strong>world</strong>" in out
    # Raw HTML in the source is escaped, never injected
    injected = str(dashboard.render_markdown("<script>alert(1)</script>"))
    assert "<script>" not in injected
