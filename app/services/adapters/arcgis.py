from datetime import datetime, timezone

import httpx

from app.services.adapters.base import (
    AdapterUnavailable,
    CityAdapter,
    PermitNotFound,
    PermitRecord,
)


def _epoch_ms_to_date(value) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%B %d, %Y")
    except (ValueError, TypeError, OSError):
        return str(value)


class ArcGISAdapter(CityAdapter):
    """Queries an ArcGIS FeatureServer/MapServer layer. Jurisdiction config:
    source.query_url  — layer /query endpoint
    source.fields     — maps permit_number/status/address/description/date -> layer field names
    source.date_is_epoch_ms — ArcGIS timestamps are epoch millis
    """

    def lookup(self, permit_number: str) -> PermitRecord:
        source = self.jurisdiction["source"]
        fields = source["fields"]
        permit_number = permit_number.strip().upper()
        where = f"UPPER({fields['permit_number']}) = '{permit_number.replace(chr(39), '')}'"

        try:
            resp = httpx.get(
                source["query_url"],
                params={
                    "where": where,
                    "outFields": "*",
                    "f": "json",
                    "resultRecordCount": 1,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"{self.jurisdiction['name']} data source unreachable: {exc}")

        if "error" in data:
            raise AdapterUnavailable(f"{self.jurisdiction['name']} query error: {data['error']}")

        features = data.get("features") or []
        if not features:
            raise PermitNotFound(permit_number)

        attrs = features[0]["attributes"]
        date_raw = attrs.get(fields.get("date", ""), "")
        details = {"source": source.get("attribution", "official city open data")}
        if freshness := self._freshness(source["query_url"]):
            details["freshness"] = freshness
        return PermitRecord(
            permit_number=str(attrs.get(fields["permit_number"], permit_number)),
            jurisdiction=self.jurisdiction["slug"],
            jurisdiction_name=self.jurisdiction["name"],
            status=str(attrs.get(fields["status"]) or "Unknown"),
            address=str(attrs.get(fields.get("address", "")) or ""),
            description=str(attrs.get(fields.get("description", "")) or ""),
            status_date=_epoch_ms_to_date(date_raw) if source.get("date_is_epoch_ms") else str(date_raw or ""),
            portal_url=self.jurisdiction["portal_url"],
            details=details,
        )

    def _freshness(self, query_url: str) -> str:
        """Real layer edit time from ArcGIS metadata - never a guess. Not all
        servers publish editingInfo (Phoenix doesn't); return "" and let the
        registry fallback stand."""
        try:
            resp = httpx.get(
                query_url.rsplit("/query", 1)[0], params={"f": "json"}, timeout=10
            )
            resp.raise_for_status()
            edited = (resp.json().get("editingInfo") or {}).get("lastEditDate")
            if edited:
                day = datetime.fromtimestamp(
                    edited / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
                return f"Official city data feed, source last updated {day}"
        except (httpx.HTTPError, ValueError):
            pass
        return ""
