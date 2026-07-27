"""News blueprint: /news and /news/refresh (RSS feeds + Spotify new releases)."""

from flask import Blueprint, render_template, redirect

from services import get_news_data, clear_news_cache

news_bp = Blueprint("news", __name__)


@news_bp.route("/news")
def news():
    data = get_news_data()
    return render_template(
        "news.html",
        articles=data["articles"],
        releases=data["releases"],
        releases_status=data.get("releases_status", "unavailable"),
        sources=data.get("sources", []),
    )


@news_bp.route("/news/refresh", methods=["POST"])
def news_refresh():
    clear_news_cache()
    return redirect("/news")
