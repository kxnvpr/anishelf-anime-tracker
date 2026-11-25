import os
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

from database import (
    create_track,
    delete_track,
    get_track,
    list_tracks,
    update_track,
)
from catalog import (
    CatalogError,
    search_catalog,
    seasonal_anime,
    top_anime,
    top_rated_anime,
    trending_anime,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "anishelf-local-dev-key")

STATUSES = {
    "watching": "Дивлюся",
    "reading": "Читаю",
    "plan": "У планах",
    "completed": "Завершено",
    "paused": "На паузі",
    "dropped": "Покинуто",
}


@app.get("/")
def index():
    season = {"name": "Сезонне аніме", "name_uk": "", "items": [], "count": 0}
    trending, top_rated = [], []
    catalog_count = 0
    catalog_error = False
    loaders = {
        "season": seasonal_anime,
        "trending": trending_anime,
        "top_rated": top_rated_anime,
    }
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {key: executor.submit(loader) for key, loader in loaders.items()}
        for key, future in futures.items():
            try:
                result = future.result()
                if key == "season":
                    season = result
                elif key == "trending":
                    trending, _ = result
                else:
                    top_rated, catalog_count = result
            except CatalogError:
                catalog_error = True

    tracks = list_tracks()
    watching = [track for track in tracks if track["status"] in {"watching", "reading"}]
    activity = sorted(tracks, key=lambda track: track["updated_at"], reverse=True)[:6]
    watch_hours = round(sum(track["progress"] for track in tracks if track["media_type"] == "anime") * 24 / 60, 1)
    return render_template(
        "index.html",
        season=season,
        trending=trending,
        top_rated=top_rated,
        catalog_count=catalog_count,
        total_saved=len(tracks),
        watch_hours=watch_hours,
        watching=watching[:6],
        activity=activity,
        statuses=STATUSES,
        catalog_error=catalog_error,
    )


@app.get("/catalog")
def catalog():
    results = []
    query = request.args.get("q", "").strip()
    media_type = request.args.get("type", "anime")
    error = None

    if media_type not in {"anime", "manga"}:
        media_type = "anime"
    if query:
        try:
            results = search_catalog(query, media_type)
            saved = {
                (track["media_type"], track["external_id"]): track
                for track in list_tracks()
            }
            for item in results:
                item["saved_track"] = saved.get((item["media_type"], item["external_id"]))
        except CatalogError as exc:
            error = str(exc)
    elif request.args.get("q") is not None:
        error = "Введи назву для пошуку."

    featured = top_anime() if not query else []
    return render_template(
        "catalog.html",
        results=results,
        query=query,
        media_type=media_type,
        error=error,
        featured=featured,
        statuses=STATUSES,
    )


@app.get("/api/suggestions")
def api_suggestions():
    query = request.args.get("q", "").strip()
    media_type = request.args.get("type", "anime")
    if len(query) < 3:
        return jsonify({"data": []})
    if media_type not in {"anime", "manga"}:
        return api_error("type must be anime or manga", 400)
    try:
        items = search_catalog(query, media_type)[:5]
    except CatalogError:
        return jsonify({"data": []})
    return jsonify({"data": [{"title": item["title"], "year": item["year"], "score": item["score"]} for item in items]})


@app.post("/library/add")
def add_to_library():
    try:
        media_type = request.form.get("media_type", "")
        if media_type not in {"anime", "manga"}:
            raise ValueError("Невідомий тип запису.")
        track = {
            "media_type": media_type,
            "external_id": int(request.form.get("external_id", "")),
            "title": request.form.get("title", "").strip(),
            "image_url": request.form.get("image_url", "").strip(),
            "synopsis": request.form.get("synopsis", "").strip(),
            "score": request.form.get("score") or None,
            "total_units": request.form.get("total_units") or None,
            "status": request.form.get("status", "plan"),
            "progress": int(request.form.get("total_units") or 0)
            if request.form.get("status") == "completed"
            else 0,
            "notes": "",
        }
        if track["status"] not in STATUSES:
            raise ValueError("Невідомий статус запису.")
        create_track(track)
        flash("Додано до твоєї бібліотеки.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            flash("Цей тайтл уже є у твоїй бібліотеці.", "error")
        else:
            raise
    return redirect(request.referrer or url_for("library"))


@app.get("/library")
def library():
    filters = {
        "q": request.args.get("q", "").strip(),
        "status": request.args.get("status", ""),
        "type": request.args.get("type", ""),
        "sort": request.args.get("sort", "recent"),
    }
    tracks = list_tracks(**filters)
    all_tracks = list_tracks()
    total = len(all_tracks)
    completed = sum(1 for row in all_tracks if row["status"] == "completed")
    anime_count = sum(1 for row in all_tracks if row["media_type"] == "anime")
    watching_count = sum(1 for row in all_tracks if row["status"] == "watching")
    watched_hours = round(sum(row["progress"] for row in all_tracks if row["media_type"] == "anime") * 24 / 60, 1)
    otaku_level = min(99, 1 + anime_count + sum(row["progress"] for row in all_tracks) // 12)
    watching_tracks = [row for row in all_tracks if row["status"] == "watching" or (row["media_type"] == "manga" and row["status"] == "reading")]
    completed_tracks = [row for row in all_tracks if row["status"] == "completed"]
    planned_tracks = [row for row in all_tracks if row["status"] == "plan"]
    other_tracks = [row for row in all_tracks if row["status"] in {"paused", "dropped"}]
    return render_template(
        "library.html",
        tracks=tracks,
        filters=filters,
        statuses=STATUSES,
        total=total,
        completed=completed,
        anime_count=anime_count,
        watching_count=watching_count,
        watched_hours=watched_hours,
        otaku_level=otaku_level,
        watching_tracks=watching_tracks,
        completed_tracks=completed_tracks,
        planned_tracks=planned_tracks,
        other_tracks=other_tracks,
    )


@app.get("/track/<int:track_id>")
def track_detail(track_id):
    track = get_track(track_id)
    if track is None:
        abort(404)
    return render_template("detail.html", track=track, statuses=STATUSES)


@app.post("/track/<int:track_id>/update")
def edit_track(track_id):
    if get_track(track_id) is None:
        abort(404)
    try:
        progress = int(request.form.get("progress", "0"))
        total = request.form.get("total_units") or None
        if progress < 0 or (total and progress > int(total)):
            raise ValueError
        status = request.form.get("status", "")
        if status not in STATUSES:
            raise ValueError
        update_track(
            track_id,
            {
                "progress": progress,
                "total_units": total,
                "status": status,
                "notes": request.form.get("notes", "").strip(),
            },
        )
        flash("Зміни збережено.", "success")
    except (TypeError, ValueError):
        flash("Перевір прогрес і статус.", "error")
    return redirect(url_for("track_detail", track_id=track_id))


@app.post("/track/<int:track_id>/delete")
def remove_track(track_id):
    if not delete_track(track_id):
        abort(404)
    flash("Запис видалено.", "success")
    return redirect(url_for("library"))


def api_error(message, status):
    return jsonify({"error": message}), status


@app.get("/api/tracks")
def api_tracks():
    media_type = request.args.get("type", "")
    status = request.args.get("status", "")
    if media_type and media_type not in {"anime", "manga"}:
        return api_error("type must be anime or manga", 400)
    if status and status not in STATUSES:
        return api_error("status is not supported", 400)
    tracks = list_tracks(
        q=request.args.get("q", "").strip(),
        type=media_type,
        status=status,
        sort=request.args.get("sort", "recent"),
    )
    return jsonify({"data": tracks, "count": len(tracks)})


@app.get("/api/tracks/<int:track_id>")
def api_track(track_id):
    track = get_track(track_id)
    return (jsonify(track), 200) if track else api_error("Track not found", 404)


@app.post("/api/tracks")
def api_create_track():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error("Request body must be a JSON object", 400)
    try:
        track = create_track(payload)
    except (ValueError, TypeError) as exc:
        return api_error(str(exc), 400)
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return api_error("Track already exists", 400)
        raise
    return jsonify(track), 201


@app.patch("/api/tracks/<int:track_id>")
def api_update_track(track_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return api_error("Request body must be a non-empty JSON object", 400)
    try:
        track = update_track(track_id, payload)
    except (ValueError, TypeError) as exc:
        return api_error(str(exc), 400)
    return (jsonify(track), 200) if track else api_error("Track not found", 404)


@app.delete("/api/tracks/<int:track_id>")
def api_delete_track(track_id):
    return (jsonify({"message": "Track deleted"}), 200) if delete_track(track_id) else api_error("Track not found", 404)


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return api_error("Resource not found", 404)
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
