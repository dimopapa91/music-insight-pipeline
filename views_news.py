"""News blueprint: /news (independent, scene-focused publications) and
/news/refresh. Optional ?scene=<key> filters to one scene."""

from flask import Blueprint, render_template, redirect, request

from services import get_news_data, clear_news_cache

news_bp = Blueprint("news", __name__)


@news_bp.route("/news")
def news():
    data = get_news_data()
    scenes = data.get("scenes", {})
    scene = request.args.get("scene", "")
    if scene not in scenes:
        scene = ""
    articles = data.get("articles", [])
    if scene:
        articles = [a for a in articles if a.get("scene") == scene]
    return render_template(
        "news.html",
        articles=articles,
        scenes=scenes,
        counts=data.get("counts", {}),
        scene=scene,
        sources=data.get("sources", []),
    )


@news_bp.route("/news/refresh", methods=["POST"])
def news_refresh():
    clear_news_cache()
    return redirect("/news")
