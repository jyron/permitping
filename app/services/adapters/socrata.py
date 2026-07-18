"""Generic Socrata (SODA) open-data adapter - one adapter for any city that
publishes per-permit records on a Socrata portal (NYC, Chicago, LA, ...).

source config:
  attribution - shown as the record's source
  datasets    - ordered list, first dataset with a hit wins (lets one city
                span two systems, e.g. NYC DOB NOW + legacy BIS). Each:
    query_url - https://<domain>/resource/<dataset-id>.json
    fields    - permit_number and status required; address is a list of
                column names joined with spaces; description/date optional.
                date also drives "$order ... DESC" so renewals/sequences
                resolve to the latest row.
"""

import httpx

from app.services.adapters.aca import UA
from app.services.adapters.base import (
    AdapterUnavailable,
    CityAdapter,
    PermitNotFound,
    PermitRecord,
)


class SocrataAdapter(CityAdapter):
    def lookup(self, permit_number: str) -> PermitRecord:
        source = self.jurisdiction["source"]
        permit_number = permit_number.strip().upper()
        last_error = None

        for dataset in source["datasets"]:
            fields = dataset["fields"]
            params = {fields["permit_number"]: permit_number, "$limit": "1"}
            if fields.get("date"):
                params["$order"] = f"{fields['date']} DESC"
            try:
                resp = httpx.get(
                    dataset["query_url"],
                    params=params,
                    headers={"User-Agent": UA},
                    timeout=20,
                )
                resp.raise_for_status()
                rows = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
            if rows:
                return self._record(rows[0], fields, source)

        if last_error:
            raise AdapterUnavailable(
                f"{self.jurisdiction['name']} data source unreachable: {last_error}"
            )
        raise PermitNotFound(permit_number)

    def _record(self, row: dict, fields: dict, source: dict) -> PermitRecord:
        address_cols = fields.get("address") or []
        address = " ".join(
            part for c in address_cols if (part := str(row.get(c) or "").strip())
        )
        return PermitRecord(
            permit_number=str(row.get(fields["permit_number"], "")),
            jurisdiction=self.jurisdiction["slug"],
            jurisdiction_name=self.jurisdiction["name"],
            status=str(row.get(fields["status"]) or "Unknown"),
            address=address,
            description=str(row.get(fields.get("description", "")) or ""),
            status_date=str(row.get(fields.get("date", "")) or "")[:10],
            portal_url=self.jurisdiction["portal_url"],
            details={"source": source.get("attribution", "official city open data")},
        )
