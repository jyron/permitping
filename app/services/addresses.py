"""Address autocomplete and permits-at-address, backed by the cities' own
permit datasets — every suggestion is an address that actually has permits.

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
import re
import time
from datetime import datetime, timezone

import httpx

from app.registry import JURISDICTIONS, get_jurisdiction
from app.services.adapters.aca import UA

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


async def _fetch_json(url: str, params: dict):
    resp = await _get_client().get(url, params=params)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- suggest

async def _suggest_socrata(jurisdiction, entry, norm_q, house, street):
    dataset = _entry_dataset(jurisdiction, entry)
    fields = dataset["fields"]
    params = {"$limit": str(PER_SOURCE_ROWS)}
    if fields.get("date"):
        params["$order"] = f"{fields['date']} DESC"
    if entry.get("full"):
        conditions = _full_column_conditions(
            f"upper({entry['full']})", house, street
        )
        if not conditions:
            return []
        params["$where"] = " AND ".join(conditions)
    else:
        if not street:
            return []
        safe = street[0].replace("'", "''")
        params["$where"] = f"upper({entry['street']}) like '{safe}%'"
        if house and entry.get("house"):
            params[entry["house"]] = house
    cols = _entry_columns(jurisdiction, entry)
    rows = await _fetch_json(dataset["query_url"], params)
    return [_row_suggestion(jurisdiction, entry, row, cols) for row in rows]


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


async def suggest(q: str) -> list[dict]:
    norm_q = _normalize(q)
    if len(norm_q) < SUGGEST_MIN_CHARS:
        return []
    if cached := _cache.get(norm_q):
        ts, results = cached
        if time.monotonic() - ts < _CACHE_TTL:
            return results

    house, street = _parse_query(norm_q)
    tasks = []
    for jurisdiction, entry in _searchable():
        if not entry.get("suggest", True):
            continue
        fn = (_suggest_socrata if jurisdiction["adapter"] == "socrata"
              else _suggest_arcgis)
        tasks.append(fn(jurisdiction, entry, norm_q, house, street))
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    seen, results = set(), []
    for batch in gathered:
        if isinstance(batch, BaseException):
            continue  # one slow/broken source never blocks the rest
        for s in batch:
            key = (s["slug"], s["address"])
            if s["address"] and key not in seen:
                seen.add(key)
                results.append(s)
    # addresses that literally start with what was typed rank first
    results.sort(key=lambda s: (not s["address"].startswith(norm_q), s["address"]))
    results = results[:SUGGEST_LIMIT]

    # empty results are usually a source timing out, not a real "no match" —
    # never cache them, so the next keystroke retries
    if results:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[norm_q] = (time.monotonic(), results)
    return results


# ---------------------------------------------------- permits at an address

async def _permits_socrata(jurisdiction, entry, filters):
    dataset = _entry_dataset(jurisdiction, entry)
    fields = dataset["fields"]
    params = {**filters, "$limit": str(PERMITS_LIMIT)}
    if fields.get("date"):
        params["$order"] = f"{fields['date']} DESC"
    rows = await _fetch_json(dataset["query_url"], params)
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
            continue
        for p in batch:
            if p["permit_number"] and p["permit_number"] not in seen:
                seen.add(p["permit_number"])
                permits.append(p)
    permits.sort(key=lambda p: p["date"] or "0000", reverse=True)
    return {
        "jurisdiction": {
            "slug": jurisdiction["slug"],
            "name": jurisdiction["name"],
            "city": jurisdiction["city"],
            "state": jurisdiction["state"],
        },
        "permits": permits[:PERMITS_LIMIT],
    }
