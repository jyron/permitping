"""Address autocomplete and permits-at-address — every suggestion is an
address that actually has permits. Suggestions come from the local permit
store first (every address-search city is feed-synced, so one SQL query
answers instantly); the cities' live APIs are the fallback for addresses the
store doesn't know, and the source of the per-dataset filters used to list
permits at a chosen address.

Jurisdictions opt in via an "address_search" list in the registry. Each entry
describes how to search one dataset:
  dataset_index - socrata only: index into source["datasets"] (query_url and
                  the permit field map are reused from there)
  full          - a single column holding the complete street address, OR
  house/street  - split columns: house number (exact match) + street name
                  (prefix match). Display columns come from the dataset's
                  fields["address"] list.
  suggest       - False to exclude from autocomplete but still include the
                  dataset when listing permits at a chosen address (used for
                  sibling datasets that share address columns, e.g. LADBS
                  electrical/mechanical).

The suggestion returned to the client carries the raw column->value pairs of
the matched row ("filters"). Listing permits re-queries every entry of that
jurisdiction whose columns cover those filter keys, so sibling datasets with
identical schemas (LA) merge into one result.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from app.db import repo
from app.db.database import SessionLocal
from app.registry import JURISDICTIONS, get_jurisdiction
from app.services.adapters.aca import UA
from app.services.adapters.socrata import socrata_headers

logger = logging.getLogger("permitping.addresses")

SUGGEST_MIN_CHARS = 4
SUGGEST_LIMIT = 8
PER_SOURCE_ROWS = 40
PERMITS_LIMIT = 25
TIMEOUT = httpx.Timeout(8.0)

_DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW",
               "NORTH", "SOUTH", "EAST", "WEST"}

_client: httpx.AsyncClient | None = None

_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 600
_CACHE_MAX = 500


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(headers={"User-Agent": UA}, timeout=TIMEOUT)
    return _client


def _searchable() -> list[tuple[dict, dict]]:
    """(jurisdiction, entry) pairs for every configured address source."""
    return [(j, e) for j in JURISDICTIONS for e in j.get("address_search", ())]


def _entry_dataset(jurisdiction: dict, entry: dict) -> dict:
    if jurisdiction["adapter"] == "socrata":
        return jurisdiction["source"]["datasets"][entry["dataset_index"]]
    return jurisdiction["source"]  # arcgis: single query_url + fields


def _entry_columns(jurisdiction: dict, entry: dict) -> list[str]:
    """Columns a suggestion's filters may reference for this entry."""
    if entry.get("full"):
        return [entry["full"]]
    fields = _entry_dataset(jurisdiction, entry)["fields"]
    address = fields["address"]
    return list(address) if isinstance(address, list) else [address]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper())


def _parse_query(q: str) -> tuple[str | None, list[str]]:
    """Split typed text into (house number, street-name tokens). Direction
    tokens (N/W/...) are dropped: cities disagree on whether and where they
    appear, so matching ignores them and the display shows the real one."""
    tokens = _normalize(q).split()
    house = tokens[0] if tokens and tokens[0].isdigit() else None
    rest = tokens[1:] if house else tokens
    street = [
        t for t in rest
        if t not in _DIRECTIONS and not t.isdigit() and len(t) >= 2
    ][:2]
    return house, street


def _format_date(value, epoch_ms: bool = False) -> str:
    if value in (None, ""):
        return ""
    if epoch_ms:
        try:
            return datetime.fromtimestamp(
                int(value) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return ""
    return str(value)[:10]


async def _fetch_json(url: str, params: dict, headers: dict | None = None):
    resp = await _get_client().get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- suggest

# Socrata portals throttle anonymous datacenter traffic hard: first the WAF
# 403s SQL-looking $where clauses, and under sustained anonymous load the IP
# gets blocked outright (a SOCRATA_APP_TOKEN largely prevents this). Precise
# $where is attempted first; on a 403 the domain is remembered and queried
# with the $q full-text parameter instead; if even $q 403s, the domain is
# hard-blocked for a while — the local permit store still serves suggestions.
_blocked_domains: dict[str, float] = {}
_hard_blocked: dict[str, float] = {}
_BLOCKED_TTL = 3600


def _domain_blocked(url: str, registry: dict | None = None) -> bool:
    registry = _blocked_domains if registry is None else registry
    ts = registry.get(httpx.URL(url).host)
    return ts is not None and time.monotonic() - ts < _BLOCKED_TTL


async def _suggest_socrata(jurisdiction, entry, norm_q, house, street):
    dataset = _entry_dataset(jurisdiction, entry)
    fields = dataset["fields"]
    base = {"$limit": str(PER_SOURCE_ROWS)}
    if fields.get("date"):
        base["$order"] = f"{fields['date']} DESC"
    cols = _entry_columns(jurisdiction, entry)

    if _domain_blocked(dataset["query_url"], _hard_blocked):
        return []  # fully banned for now; the store source still answers

    if not _domain_blocked(dataset["query_url"]):
        params = dict(base)
        if entry.get("full"):
            col = f"upper({entry['full']})"
            conditions = [f"starts_with({col}, '{house} ')"] if house else []
            conditions += [
                f"contains({col}, '{t.replace(chr(39), chr(39) * 2)}')"
                for t in street
            ]
            if not conditions:
                return []
            params["$where"] = " AND ".join(conditions)
        else:
            if not street:
                return []
            safe = street[0].replace("'", "''")
            params["$where"] = f"starts_with(upper({entry['street']}), '{safe}')"
            if house and entry.get("house"):
                params[entry["house"]] = house
        try:
            rows = await _fetch_json(dataset["query_url"], params, socrata_headers())
            return [_row_suggestion(jurisdiction, entry, row, cols) for row in rows]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            _blocked_domains[httpx.URL(dataset["query_url"]).host] = time.monotonic()
            logger.warning("WAF 403 from %s, falling back to $q", dataset["query_url"])

    # $q matches complete words only. Try all tokens first (typed words are
    # usually complete); if the last token was mid-word and nothing matched,
    # retry without it and recover precision by filtering rows in Python.
    tokens = ([house] if house else []) + street
    if not tokens:
        return []

    async def q_attempt(q_tokens):
        params = dict(base, **{"$q": " ".join(q_tokens)})
        if house and entry.get("house"):
            params[entry["house"]] = house
        try:
            rows = await _fetch_json(dataset["query_url"], params, socrata_headers())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            host = httpx.URL(dataset["query_url"]).host
            _hard_blocked[host] = time.monotonic()
            logger.warning("even $q 403s from %s — hard-blocking live queries", host)
            return []
        suggestions = [_row_suggestion(jurisdiction, entry, r, cols) for r in rows]
        return [
            s for s in suggestions
            if all(t in s["address"] for t in street)
            and (not house or s["address"].startswith(f"{house} "))
        ]

    results = await q_attempt(tokens)
    if not results and len(tokens) > 1 and not _domain_blocked(dataset["query_url"], _hard_blocked):
        results = await q_attempt(tokens[:-1])
    return results


def _full_column_conditions(col_expr: str, house, street) -> list[str]:
    """SQL-ish conditions matching one full-address column: house number as
    a prefix, street tokens as contains — works whether or not the user
    typed the direction the dataset stores."""
    conditions = []
    if house:
        conditions.append(f"{col_expr} like '{house} %'")
    for token in street:
        safe = token.replace("'", "''")
        conditions.append(f"{col_expr} like '%{safe}%'")
    return conditions


async def _suggest_arcgis(jurisdiction, entry, norm_q, house, street):
    col = entry["full"]
    conditions = _full_column_conditions(f"UPPER({col})", house, street)
    if not conditions:
        return []
    params = {
        "f": "json",
        "where": " AND ".join(conditions),
        "outFields": col,
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "resultRecordCount": str(PER_SOURCE_ROWS),
    }
    data = await _fetch_json(jurisdiction["source"]["query_url"], params)
    return [
        _row_suggestion(jurisdiction, entry, f.get("attributes", {}), [col])
        for f in data.get("features", [])
    ]


def _row_suggestion(jurisdiction, entry, row, cols):
    filters = {
        c: str(row[c]).strip() for c in cols
        if row.get(c) is not None and str(row[c]).strip()
    }
    address = _normalize(" ".join(filters.get(c, "") for c in cols))
    return {
        "address": address,
        "label": f"{address} — {jurisdiction['city']}, {jurisdiction['state']}",
        "slug": jurisdiction["slug"],
        "city": jurisdiction["city"],
        "state": jurisdiction["state"],
        "filters": filters,
    }


def _store_suggestions_sync(city_slug, house, street):
    """Suggestions from our own permit store — instant, and the only source
    that still answers when the live portals are throttling us."""
    slugs = [
        j["slug"] for j in JURISDICTIONS
        if j.get("address_search") and (not city_slug or j["slug"] == city_slug)
    ]
    if not slugs:
        return []
    db = SessionLocal()
    try:
        pairs = repo.address_suggestions_from_store(
            db, slugs, house, street, SUGGEST_LIMIT * 2
        )
    finally:
        db.close()
    results = []
    for slug, address in pairs:
        jurisdiction = get_jurisdiction(slug)
        address = _normalize(address)
        results.append({
            "address": address,
            "label": f"{address} — {jurisdiction['city']}, {jurisdiction['state']}",
            "slug": slug,
            "city": jurisdiction["city"],
            "state": jurisdiction["state"],
            "filters": {},
        })
    return results


LIVE_SUGGEST_TIMEOUT = 3.0


async def _live_suggestions(
    city_slug: str | None, norm_q: str, house: str | None, street: list[str]
) -> list[dict]:
    """Fan out to the cities' live APIs — the fallback when the store has no
    match (cold start, or an address outside the synced window). Sources that
    haven't answered within LIVE_SUGGEST_TIMEOUT are dropped, not awaited."""
    tasks = []
    for jurisdiction, entry in _searchable():
        if not entry.get("suggest", True):
            continue
        if city_slug and jurisdiction["slug"] != city_slug:
            continue
        fn = (_suggest_socrata if jurisdiction["adapter"] == "socrata"
              else _suggest_arcgis)
        label = f"{jurisdiction['slug']}#{entry.get('dataset_index', 0)}"
        tasks.append((label, asyncio.create_task(
            fn(jurisdiction, entry, norm_q, house, street))))
    if not tasks:
        return []
    await asyncio.wait([t for _, t in tasks], timeout=LIVE_SUGGEST_TIMEOUT)

    results = []
    for label, task in tasks:
        if not task.done():
            task.cancel()
            logger.warning("suggest source %s dropped after %.1fs", label, LIVE_SUGGEST_TIMEOUT)
        elif exc := task.exception():
            logger.warning("suggest source %s failed: %r", label, exc)
        else:
            results.extend(task.result())
    return results


async def suggest(q: str, city_slug: str | None = None) -> list[dict]:
    norm_q = _normalize(q)
    if len(norm_q) < SUGGEST_MIN_CHARS:
        return []
    cache_key = f"{city_slug or '*'}|{norm_q}"
    if cached := _cache.get(cache_key):
        ts, results = cached
        if time.monotonic() - ts < _CACHE_TTL:
            return results

    house, street = _parse_query(norm_q)
    # store first: every address-search city is feed-synced, so one local SQL
    # query answers instantly and consistently; live sources are only worth
    # their latency (and WAF roulette) when the store has nothing at all
    raw = await asyncio.to_thread(_store_suggestions_sync, city_slug, house, street)
    if not raw:
        raw = await _live_suggestions(city_slug, norm_q, house, street)

    seen, results = set(), []
    for s in raw:
        key = (s["slug"], s["address"])
        if s["address"] and key not in seen:
            seen.add(key)
            results.append(s)
    # addresses that literally start with what was typed rank first; the sort
    # is stable, so recency order from the store survives within each group
    results.sort(key=lambda s: not s["address"].startswith(norm_q))
    results = results[:SUGGEST_LIMIT]

    # empty results are usually a source timing out, not a real "no match" —
    # never cache them, so the next keystroke retries
    if results:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[cache_key] = (time.monotonic(), results)
    return results


# -------------------------------------------------------- address page urls

def slugify_address(address: str) -> str:
    """URL slug for an address page: alnum runs joined by dashes."""
    return "-".join(re.findall(r"[a-z0-9]+", address.lower()))


def _alnum(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


async def resolve_address_slug(city_slug: str, addr_slug: str) -> dict | None:
    """Map an address-page slug back to a live suggestion (address text +
    filters) by re-running the scoped suggest pipeline and matching on the
    alnum-normalized form (slugs drop characters like '#')."""
    text = " ".join(addr_slug.split("-"))
    suggestions = await suggest(text, city_slug=city_slug)
    target = _alnum(text)
    for s in suggestions:
        if _alnum(s["address"]) == target:
            return s
    return None


# ---------------------------------------------------- permits at an address

# address pages are crawlable, so results are cached briefly in-process to
# keep bots from hammering the city APIs
_permits_cache: dict[str, tuple[float, dict]] = {}
_PERMITS_CACHE_TTL = 6 * 3600
_PERMITS_CACHE_MAX = 2000


async def _permits_socrata(jurisdiction, entry, filters):
    dataset = _entry_dataset(jurisdiction, entry)
    fields = dataset["fields"]
    params = {**filters, "$limit": str(PERMITS_LIMIT)}
    if fields.get("date"):
        params["$order"] = f"{fields['date']} DESC"
    rows = await _fetch_json(dataset["query_url"], params, socrata_headers())
    cols = _entry_columns(jurisdiction, entry)
    return [
        {
            "permit_number": str(row.get(fields["permit_number"], "")),
            "status": str(row.get(fields["status"]) or "Unknown"),
            "description": str(row.get(fields.get("description", "")) or "")[:200],
            "date": _format_date(row.get(fields.get("date", ""))),
            "address": _normalize(
                " ".join(str(row.get(c) or "") for c in cols)
            ),
        }
        for row in rows
    ]


async def _permits_arcgis(jurisdiction, entry, filters):
    source = jurisdiction["source"]
    fields = source["fields"]
    col, value = next(iter(filters.items()))
    safe = value.replace("'", "''")
    params = {
        "f": "json",
        "where": f"UPPER({col}) = '{safe.upper()}'",
        "outFields": "*",
        "returnGeometry": "false",
        "resultRecordCount": str(PERMITS_LIMIT),
        "orderByFields": f"{fields['date']} DESC",
    }
    data = await _fetch_json(source["query_url"], params)
    epoch_ms = source.get("date_is_epoch_ms", False)
    return [
        {
            "permit_number": str(a.get(fields["permit_number"], "")),
            "status": str(a.get(fields["status"]) or "Unknown"),
            "description": str(a.get(fields.get("description", "")) or "")[:200],
            "date": _format_date(a.get(fields.get("date", "")), epoch_ms),
            "address": _normalize(str(a.get(entry["full"]) or "")),
        }
        for f in data.get("features", [])
        if (a := f.get("attributes", {}))
    ]


async def permits_at(slug: str, filters: dict) -> dict | None:
    """List permits at the address a suggestion identified. Queries every
    address_search entry of the jurisdiction whose columns cover the filter
    keys, so same-schema sibling datasets merge into one list."""
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction or not jurisdiction.get("address_search"):
        return None
    filters = {
        str(k): str(v)[:120] for k, v in filters.items() if str(v).strip()
    }
    if not filters or len(filters) > 8:
        return None

    cache_key = f"{slug}|{sorted(filters.items())}"
    if cached := _permits_cache.get(cache_key):
        ts, result = cached
        if time.monotonic() - ts < _PERMITS_CACHE_TTL:
            return result

    tasks = []
    for entry in jurisdiction["address_search"]:
        if set(filters) <= set(_entry_columns(jurisdiction, entry)):
            fn = (_permits_socrata if jurisdiction["adapter"] == "socrata"
                  else _permits_arcgis)
            tasks.append(fn(jurisdiction, entry, filters))
    if not tasks:
        return None  # filter keys aren't columns we configured: reject

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    seen, permits = set(), []
    for batch in gathered:
        if isinstance(batch, BaseException):
            logger.warning("permits_at source for %s failed: %r", slug, batch)
            continue
        for p in batch:
            if p["permit_number"] and p["permit_number"] not in seen:
                seen.add(p["permit_number"])
                permits.append(p)
    permits.sort(key=lambda p: p["date"] or "0000", reverse=True)
    result = {
        "jurisdiction": {
            "slug": jurisdiction["slug"],
            "name": jurisdiction["name"],
            "city": jurisdiction["city"],
            "state": jurisdiction["state"],
        },
        "permits": permits[:PERMITS_LIMIT],
    }
    if permits:
        if len(_permits_cache) >= _PERMITS_CACHE_MAX:
            _permits_cache.clear()
        _permits_cache[cache_key] = (time.monotonic(), result)
    return result
