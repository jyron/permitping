"""Accela Construct API — the vendor's official JSON API for agencies running
Accela (Mesa, Chandler). Used when ACCELA_APP_ID is set; the portal-scrape
adapter is passed in as a runtime fallback because agencies individually
enable/disable anonymous API access.

UNVERIFIED against live agencies until a real App ID is configured — the call
shape follows developer.accela.com/docs/api_reference/v4.get.records.html and
the live unauthenticated probe (HTTP 400 demanding x-accela-appid).
"""

from datetime import datetime

import httpx

from app import config
from app.services.adapters.base import (
    AdapterUnavailable,
    CityAdapter,
    PermitNotFound,
    PermitRecord,
)

API_BASE = "https://apis.accela.com/v4"


class AccelaConstructAdapter(CityAdapter):
    """source config: agency (e.g. MESA). fallback: a CityAdapter instance."""

    def __init__(self, jurisdiction: dict, fallback: CityAdapter | None = None):
        super().__init__(jurisdiction)
        self.fallback = fallback

    def lookup(self, permit_number: str) -> PermitRecord:
        permit_number = permit_number.strip().upper()
        headers = {
            "x-accela-appid": config.ACCELA_APP_ID,
            "x-accela-agency": self.jurisdiction["source"]["agency"],
            "x-accela-environment": "PROD",
        }
        if config.ACCELA_APP_SECRET:
            headers["x-accela-appsecret"] = config.ACCELA_APP_SECRET

        try:
            resp = httpx.get(
                f"{API_BASE}/records",
                params={"customId": permit_number},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json().get("result") or []
        except (httpx.HTTPError, ValueError) as exc:
            if self.fallback:
                return self.fallback.lookup(permit_number)
            raise AdapterUnavailable(
                f"{self.jurisdiction['name']} Accela API unavailable: {exc}"
            )

        if not result:
            raise PermitNotFound(permit_number)

        rec = next(
            (r for r in result if str(r.get("customId", "")).upper() == permit_number),
            result[0],
        )

        address = ""
        rec_id = rec.get("id")
        if rec_id:
            try:
                addr_resp = httpx.get(
                    f"{API_BASE}/records/{rec_id}/addresses", headers=headers, timeout=20
                )
                addr_resp.raise_for_status()
                addrs = addr_resp.json().get("result") or []
                if addrs:
                    a = addrs[0]
                    parts = [
                        str(a.get("streetStart", "") or a.get("houseNumberStart", "") or ""),
                        a.get("streetName", "") or "",
                        a.get("streetSuffix", {}).get("text", "") if isinstance(a.get("streetSuffix"), dict) else "",
                        a.get("city", "") or "",
                    ]
                    address = " ".join(p for p in parts if p).strip()
            except (httpx.HTTPError, ValueError):
                pass  # address is nice-to-have; status is the product

        status = rec.get("status") or {}
        status_date = str(rec.get("statusDate", ""))[:10]
        if status_date:
            try:
                status_date = datetime.fromisoformat(status_date).strftime("%B %d, %Y")
            except ValueError:
                pass

        record_type = rec.get("type") or {}
        description = rec.get("description", "") or ""
        type_text = record_type.get("text", "") if isinstance(record_type, dict) else ""

        return PermitRecord(
            permit_number=str(rec.get("customId", permit_number)),
            jurisdiction=self.jurisdiction["slug"],
            jurisdiction_name=self.jurisdiction["name"],
            status=(status.get("text") or status.get("value") or "Unknown")
            if isinstance(status, dict)
            else str(status),
            address=address,
            description=f"{type_text} - {description}" if type_text and description else (type_text or description),
            status_date=status_date,
            portal_url=self.jurisdiction["portal_url"],
            details={"source": "official Accela records API"},
        )
