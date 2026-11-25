"""Kitsu API adapter: keep remote JSON out of the application templates."""

import json
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://kitsu.io/api/edge"
_featured_cache = {"at": 0, "data": []}
_section_cache = {}


class CatalogError(Exception):
    pass


def _get_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "User-Agent": "AniShelf/1.0 (student project)",
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise CatalogError("Kitsu тимчасово обмежив запити. Зачекай трохи й спробуй ще раз.") from None
        raise CatalogError(f"Каталог повернув помилку ({exc.code}). Спробуй пізніше.") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise CatalogError("Каталог аніме зараз недоступний. Перевір інтернет або спробуй пізніше.") from None


def _normalise(item, media_type, categories=None):
    attrs = item.get("attributes") or {}
    titles = attrs.get("titles") or {}
    poster = attrs.get("posterImage") or {}
    try:
        score = round(float(attrs["averageRating"]) / 10, 1) if attrs.get("averageRating") else None
    except (TypeError, ValueError):
        score = None

    return {
        "external_id": int(item["id"]),
        "media_type": media_type,
        "title": attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or titles.get("ja_jp") or "Без назви",
        "original_title": titles.get("ja_jp") or attrs.get("canonicalTitle") or "",
        "image_url": poster.get("large") or poster.get("medium") or poster.get("original") or "",
        "synopsis": attrs.get("synopsis") or attrs.get("description") or "Опис поки відсутній.",
        "score": score,
        "total_units": attrs.get("episodeCount") if media_type == "anime" else attrs.get("chapterCount"),
        "year": (attrs.get("startDate") or "")[:4] or None,
        "genres": (categories or [])[:3],
        "status": attrs.get("status") or "",
    }


def _category_names(payload, item):
    ids = {
        relation.get("id")
        for relation in ((item.get("relationships") or {}).get("categories") or {}).get("data", [])
    }
    return [
        (included.get("attributes") or {}).get("title")
        for included in payload.get("included", [])
        if included.get("type") == "categories" and included.get("id") in ids
        and (included.get("attributes") or {}).get("title")
    ]


def search_catalog(query, media_type):
    params = urlencode({"filter[text]": query, "page[limit]": 12, "include": "categories"})
    payload = _get_json(f"{BASE_URL}/{media_type}?{params}")
    return [
        _normalise(item, media_type, _category_names(payload, item))
        for item in payload.get("data", [])
    ]


def top_anime(limit=4):
    """Return a short, cached selection for the home page's featured rail."""
    if time.time() - _featured_cache["at"] < 1800:
        return _featured_cache["data"][:limit]
    params = urlencode({"sort": "-userCount", "page[limit]": limit})
    try:
        payload = _get_json(f"{BASE_URL}/anime?{params}")
    except CatalogError:
        _featured_cache.update(at=time.time(), data=[])
        return []
    items = [_normalise(item, "anime") for item in payload.get("data", [])]
    _featured_cache.update(at=time.time(), data=items)
    return items[:limit]


def _cached_collection(key, params, limit):
    cached = _section_cache.get(key)
    if cached and time.time() - cached["at"] < 1800:
        if cached.get("error"):
            raise CatalogError("Kitsu тимчасово недоступний")
        return cached["data"][:limit], cached["count"]
    try:
        params = {**params, "include": "categories"}
        payload = _get_json(f"{BASE_URL}/anime?{urlencode(params)}")
    except CatalogError:
        _section_cache[key] = {"at": time.time(), "data": [], "count": 0, "error": True}
        raise
    items = [
        _normalise(item, "anime", _category_names(payload, item))
        for item in payload.get("data", [])
    ]
    try:
        count = int((payload.get("meta") or {}).get("count", len(items)))
    except (TypeError, ValueError):
        count = len(items)
    _section_cache[key] = {"at": time.time(), "data": items, "count": count}
    return items[:limit], count


def seasonal_anime(limit=12):
    now = datetime.now()
    season = "winter" if now.month <= 3 else "spring" if now.month <= 6 else "summer" if now.month <= 9 else "fall"
    season_uk = {"winter": "Зима", "spring": "Весна", "summer": "Літо", "fall": "Осінь"}[season]
    params = {
        "filter[season]": season,
        "filter[seasonYear]": now.year,
        "sort": "-userCount",
        "page[limit]": limit,
    }
    items, count = _cached_collection("season", params, limit)
    return {"name": f"{season.title()} {now.year}", "name_uk": f"{season_uk} {now.year}", "items": items, "count": count}


def trending_anime(limit=12):
    return _cached_collection(
        "trending",
        {"filter[status]": "current", "sort": "-userCount", "page[limit]": limit},
        limit,
    )


def top_rated_anime(limit=12):
    return _cached_collection(
        "top-rated",
        {"sort": "-averageRating", "page[limit]": limit},
        limit,
    )
