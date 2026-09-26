"""Genres blueprint: a curated set of genre pages built from Last.fm tags.

/genres          every genre, each fronted by its top artist's photo
/genre/<slug>    Last.fm's description + top artists for that tag, with
                 photos, and which of them are already analysed on Waveline

Only the slugs in services.GENRES resolve (404 otherwise), so these pages
can't be used to drive arbitrary Last.fm traffic. Everything is cached in
services; no Claude calls and no pipeline runs happen here.
"""

from flask import Blueprint, render_template, abort

from rate_limit import limiter
from services import (GENRES, get_genre, get_genre_covers, get_artist_photos,
                      analysed_artist_names)

genres_bp = Blueprint("genres", __name__)


@genres_bp.route("/genres")
@limiter.limit("60 per hour")
def genres_index():
    return render_template("genres.html", covers=get_genre_covers())


@genres_bp.route("/genre/<slug>")
@limiter.limit("60 per hour")
def genre_page(slug):
    genre = get_genre(slug)
    if genre is None:
        abort(404)
    names = genre["artists"]
    photos = get_artist_photos(names)
    analysed = analysed_artist_names(names)
    artists = [{
        "name": n,
        "image": photos.get(n, {}).get("image", ""),
        "fans": photos.get(n, {}).get("nb_fan", 0),
        "analysed": n.lower() in analysed,
    } for n in names]
    others = [g for g in GENRES if g["slug"] != slug]
    return render_template("genre.html", genre=genre, artists=artists, others=others,
                           analysed_count=sum(a["analysed"] for a in artists))
