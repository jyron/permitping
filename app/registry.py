"""Municipality registry — the single source of truth for supported cities.

Each entry declares:
- source_class: "feed" (bulk-syncable dataset -> local permit store) or
  "portal" (interactive lookup only, one permit per request)
- zips / zip_prefixes: for resolving a user-entered ZIP code to the city
- adapter + source: how to fetch from the city's system

Onboarding a city means adding one entry here. Nothing else changes.
"""

from app import config
from app.services.adapters.aca import AcaGlobalSearchAdapter, AcaViewstateAdapter
from app.services.adapters.accela_api import AccelaConstructAdapter
from app.services.adapters.arcgis import ArcGISAdapter
from app.services.adapters.base import AdapterUnavailable, CityAdapter
from app.services.adapters.socrata import SocrataAdapter

# LADBS "from 2020 to Present" datasets all share one schema; the same field
# map serves building, electrical, and mechanical, issued and submitted.
_LADBS_FIELDS = {
    "permit_number": "permit_nbr",
    "status": "status_desc",
    "address": ["primary_address"],
    "description": "work_desc",
    "date": "status_date",
}

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
        "freshness": "Official city data feed",
        "adapter": "arcgis",
        "address_search": [{"full": "OriginalAddress1"}],
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
        "zip_prefixes": ["850"],
        "portal_name": "Phoenix PDD Online Permit Search",
        "portal_url": "https://apps-secure.phoenix.gov/PDD/Search/Permits",
        "permit_example": "26008790",
        "freshness": "Official city GIS feed (covers roughly the last 2 years; newest SHAPE PHX CTR- permits not yet included)",
        "adapter": "arcgis",
        "address_search": [{"full": "STREET_FULL_NAME"}],
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
    {
        "slug": "goodyear-az",
        "name": "Goodyear, Arizona",
        "city": "Goodyear",
        "state": "AZ",
        "source_class": "portal",
        "zips": ["85338", "85395"],
        "portal_name": "Goodyear Services Portal (Accela)",
        "portal_url": "https://aca-prod.accela.com/GOODYEAR/",
        "permit_example": "B26-01594",
        "freshness": "Live official portal, real-time",
        "adapter": "aca_global",
        "source": {
            "base_url": "https://aca-prod.accela.com/GOODYEAR",
            "agency": "GOODYEAR",
        },
    },
    {
        "slug": "new-york-ny",
        "name": "New York City, New York",
        "city": "New York",
        "state": "NY",
        "aliases": ["nyc", "new york city", "manhattan", "brooklyn", "queens",
                    "bronx", "the bronx", "staten island"],
        "source_class": "portal",
        "zip_prefixes": ["100", "101", "102", "103", "104", "111", "112",
                         "113", "114", "116"],
        "portal_name": "NYC Department of Buildings (DOB NOW / BIS)",
        "portal_url": "https://a810-dobnow.nyc.gov/publish/Index.html",
        "permit_example": "B00354263-I1",
        "freshness": "Official NYC open data, updated daily per NYC DOB",
        "adapter": "socrata",
        "address_search": [
            {"dataset_index": 0, "house": "house_no", "street": "street_name"},
            {"dataset_index": 1, "house": "house__", "street": "street_name"},
        ],
        "source": {
            "attribution": "NYC Open Data (Department of Buildings)",
            "datasets": [
                {
                    "query_url": "https://data.cityofnewyork.us/resource/rbx6-tga4.json",
                    "fields": {
                        "permit_number": "job_filing_number",
                        "status": "permit_status",
                        "address": ["house_no", "street_name", "borough"],
                        "description": "job_description",
                        "date": "issued_date",
                    },
                },
                {
                    "query_url": "https://data.cityofnewyork.us/resource/ipu4-2q9a.json",
                    "fields": {
                        "permit_number": "job__",
                        "status": "permit_status",
                        "address": ["house__", "street_name", "borough"],
                        "description": "job_type",
                        "date": "issuance_date",
                    },
                },
            ],
        },
    },
    {
        "slug": "chicago-il",
        "name": "Chicago, Illinois",
        "city": "Chicago",
        "state": "IL",
        "source_class": "portal",
        "zip_prefixes": ["606"],
        "portal_name": "Chicago Building Permits (city data portal)",
        "portal_url": "https://data.cityofchicago.org/Buildings/Building-Permits/ydr8-5enu",
        "permit_example": "B200477893",
        "freshness": "Official city open data, updated daily per city data portal",
        "adapter": "socrata",
        "address_search": [
            {"dataset_index": 0, "house": "street_number", "street": "street_name"},
        ],
        "source": {
            "attribution": "Chicago Data Portal (Department of Buildings)",
            "datasets": [
                {
                    "query_url": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
                    "fields": {
                        "permit_number": "permit_",
                        "status": "permit_status",
                        "address": ["street_number", "street_direction", "street_name"],
                        "description": "work_description",
                        "date": "issue_date",
                    },
                },
            ],
        },
    },
    {
        "slug": "los-angeles-ca",
        "name": "Los Angeles, California",
        "city": "Los Angeles",
        "state": "CA",
        "aliases": ["la"],
        "source_class": "portal",
        "zip_prefixes": ["900", "901", "913", "914"],
        "portal_name": "LADBS Online Permit Lookup",
        "portal_url": "https://www.ladbs.org/services/check-status/online-permit-lookup",
        "permit_example": "26016-90000-18307",
        "freshness": "Official city open data (LADBS)",
        "adapter": "socrata",
        # suggest from building-issued only; on selection the sibling LADBS
        # datasets (same schema) are queried too and merged into one list
        "address_search": [
            {"dataset_index": 0, "full": "primary_address"},
            *({"dataset_index": i, "full": "primary_address", "suggest": False}
              for i in range(1, 6)),
        ],
        "source": {
            "attribution": "Los Angeles Open Data (LADBS)",
            # issued first (later lifecycle state wins), then submitted
            # (in-review permits), then the retired pre-2020 archive.
            # Covers building, electrical, and mechanical permits; LADBS
            # publishes no separate plumbing dataset.
            "datasets": [
                *(
                    {
                        "query_url": f"https://data.lacity.org/resource/{dataset_id}.json",
                        "fields": _LADBS_FIELDS,
                    }
                    for dataset_id in (
                        "pi9x-tg5x",  # building issued
                        "ysqd-apz7",  # electrical issued
                        "67is-svtd",  # mechanical issued
                        "gwh9-jnip",  # building submitted
                        "9k3p-zrda",  # electrical submitted
                        "9rag-xmmd",  # mechanical submitted
                    )
                ),
                {
                    "query_url": "https://data.lacity.org/resource/xnhu-aczu.json",
                    "fields": {
                        "permit_number": "pcis_permit",
                        "status": "latest_status",
                        "address": ["address_start", "street_direction",
                                    "street_name", "street_suffix"],
                        "description": "work_description",
                        "date": "status_date",
                    },
                },
            ],
        },
    },
]

_BY_SLUG = {j["slug"]: j for j in JURISDICTIONS}

_ADAPTERS = {
    "aca_viewstate": AcaViewstateAdapter,
    "aca_global": AcaGlobalSearchAdapter,
    "arcgis": ArcGISAdapter,
    "socrata": SocrataAdapter,
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
