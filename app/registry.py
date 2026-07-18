"""Municipality registry — the single source of truth for supported cities.

Each entry declares:
- source_class: "feed" (bulk-syncable dataset -> local permit store) or
  "portal" (interactive lookup only, one permit per request)
- zips / zip_prefix: for resolving a user-entered ZIP code to the city
- adapter + source: how to fetch from the city's system

Onboarding a city means adding one entry here. Nothing else changes.
"""

from app import config
from app.services.adapters.aca import AcaGlobalSearchAdapter, AcaViewstateAdapter
from app.services.adapters.accela_api import AccelaConstructAdapter
from app.services.adapters.arcgis import ArcGISAdapter
from app.services.adapters.base import AdapterUnavailable, CityAdapter

JURISDICTIONS = [
    {
        "slug": "mesa-az",
        "name": "Mesa, Arizona",
        "city": "Mesa",
        "state": "AZ",
        "source_class": "portal",
        "zips": ["85201", "85202", "85203", "85204", "85205", "85206", "85207",
                 "85208", "85209", "85210", "85212", "85213", "85215"],
        "portal_name": "Mesa Accela Citizen Access",
        "portal_url": "https://aca-prod.accela.com/MESA/Cap/CapHome.aspx?module=Permits&TabName=Permits",
        "permit_example": "PMT23-16945",
        "freshness": "Live official portal, real-time",
        "adapter": "aca_viewstate",
        "source": {
            "base_url": "https://aca-prod.accela.com/MESA",
            "module": "Permits",
            "agency": "MESA",
        },
    },
    {
        "slug": "tempe-az",
        "name": "Tempe, Arizona",
        "city": "Tempe",
        "state": "AZ",
        "source_class": "feed",
        "zips": ["85281", "85282", "85283", "85284", "85285", "85287"],
        "portal_name": "Tempe Accela Citizen Access",
        "portal_url": "https://epermits.tempe.gov/CitizenAccess/Cap/CapHome.aspx?module=Building&TabName=Building",
        "permit_example": "BP261596",
        "freshness": "Official city data feed, refreshed about daily",
        "adapter": "arcgis",
        "source": {
            "query_url": "https://services.arcgis.com/lQySeXwbBg53XWDi/arcgis/rest/services/building_permits/FeatureServer/0/query",
            "fields": {
                "permit_number": "PermitNum",
                "status": "StatusCurrent",
                "address": "OriginalAddress1",
                "description": "Description",
                "date": "StatusDate",
            },
            "attribution": "City of Tempe open data (extracted from Accela)",
        },
    },
    {
        "slug": "chandler-az",
        "name": "Chandler, Arizona",
        "city": "Chandler",
        "state": "AZ",
        "source_class": "portal",
        "zips": ["85224", "85225", "85226", "85244", "85246", "85248", "85249", "85286"],
        "portal_name": "Chandler Accela Citizen Access",
        "portal_url": "https://aca-prod.accela.com/CHANDLER/",
        "permit_example": "BLD26-1722",
        "freshness": "Live official portal, real-time",
        "adapter": "aca_global",
        "source": {
            "base_url": "https://aca-prod.accela.com/CHANDLER",
            "agency": "CHANDLER",
        },
    },
    {
        "slug": "phoenix-az",
        "name": "Phoenix, Arizona",
        "city": "Phoenix",
        "state": "AZ",
        "source_class": "feed",
        "zip_prefix": "850",
        "portal_name": "Phoenix PDD Online Permit Search",
        "portal_url": "https://apps-secure.phoenix.gov/PDD/Search/Permits",
        "permit_example": "26008790",
        "freshness": "Official city feed, refreshed about daily (covers the last ~2 years; newest SHAPE PHX CTR- permits not yet included)",
        "adapter": "arcgis",
        "source": {
            "query_url": "https://maps.phoenix.gov/pub/rest/services/Public/Planning_Permit/MapServer/1/query",
            "fields": {
                "permit_number": "PER_NUM",
                "status": "PERMIT_STAT",
                "address": "STREET_FULL_NAME",
                "description": "PERMIT_NAME",
                "date": "PER_ISSUE_DATE",
            },
            "date_is_epoch_ms": True,
            "attribution": "City of Phoenix GIS",
        },
    },
]

_BY_SLUG = {j["slug"]: j for j in JURISDICTIONS}

_ADAPTERS = {
    "aca_viewstate": AcaViewstateAdapter,
    "aca_global": AcaGlobalSearchAdapter,
    "arcgis": ArcGISAdapter,
}


def get_jurisdiction(slug: str) -> dict | None:
    return _BY_SLUG.get(slug)


def feed_jurisdictions() -> list[dict]:
    return [j for j in JURISDICTIONS if j["source_class"] == "feed"]


def get_adapter(slug: str) -> CityAdapter:
    jurisdiction = _BY_SLUG.get(slug)
    if not jurisdiction:
        raise AdapterUnavailable(f"Unsupported jurisdiction: {slug}")
    adapter = _ADAPTERS[jurisdiction["adapter"]](jurisdiction)
    # Accela agencies: prefer the vendor's official JSON API when a key is
    # configured; the portal adapter stays as runtime fallback.
    if config.ACCELA_APP_ID and jurisdiction["source"].get("agency"):
        return AccelaConstructAdapter(jurisdiction, fallback=adapter)
    return adapter
