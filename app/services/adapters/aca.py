"""Adapters for Accela Citizen Access (ACA) portals — the live municipal
system of record, queried anonymously. Two flavors:

- AcaGlobalSearchAdapter: portals where GlobalSearchResults.aspx?QueryText=N
  302-redirects straight to the record's CapDetail page (e.g. Chandler).
- AcaViewstateAdapter: portals where global search is disabled and the permit
  search form must be POSTed with the page's ASP.NET viewstate (e.g. Mesa).
"""

import html as html_lib
import re

import httpx

from app.services.adapters.base import (
    AdapterUnavailable,
    CityAdapter,
    PermitNotFound,
    PermitRecord,
)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0"


def _strip(fragment: str) -> str:
    text = re.sub(r"<script.*?</script>", "", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = html_lib.unescape(text)
    return re.sub(r"[\s|]*\|[\s|]*", "|", text).strip("|")


def _span(page: str, span_id: str) -> str:
    m = re.search(rf'id="{span_id}"[^>]*>(.*?)</', page, re.S)
    return html_lib.unescape(m.group(1)).strip() if m else ""


class AcaGlobalSearchAdapter(CityAdapter):
    """source config: base_url (e.g. https://aca-prod.accela.com/CHANDLER)"""

    def lookup(self, permit_number: str) -> PermitRecord:
        base = self.jurisdiction["source"]["base_url"]
        permit_number = permit_number.strip().upper()

        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=30, verify=True) as client:
                resp = client.get(
                    f"{base}/Cap/GlobalSearchResults.aspx",
                    params={"QueryText": permit_number},
                )
                if resp.status_code == 200:
                    raise PermitNotFound(permit_number)
                if resp.status_code not in (301, 302):
                    raise AdapterUnavailable(
                        f"{self.jurisdiction['name']} portal returned HTTP {resp.status_code}"
                    )
                detail_url = str(resp.next_request.url)
                page = client.get(detail_url).raise_for_status().text
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(f"{self.jurisdiction['name']} portal unreachable: {exc}")

        status = _span(page, "ctl00_PlaceHolderMain_lblRecordStatus")
        if not status:
            raise PermitNotFound(permit_number)

        address = ""
        m = re.search(
            r'<table id="ctl00_PlaceHolderMain_workLocation[^"]*".*?</table>', page, re.S
        )
        if m:
            address = " ".join(p for p in _strip(m.group(0)).split("|") if p != "*")

        description = ""
        i = page.find("Project Description")
        if i >= 0:
            parts = _strip(page[i : i + 3000]).split("|")
            description = " — ".join(parts[1:3]) if len(parts) > 2 else "|".join(parts[1:2])

        return PermitRecord(
            permit_number=_span(page, "ctl00_PlaceHolderMain_lblPermitNumber") or permit_number,
            jurisdiction=self.jurisdiction["slug"],
            jurisdiction_name=self.jurisdiction["name"],
            status=status,
            address=address,
            description=(
                f"{_span(page, 'ctl00_PlaceHolderMain_lblPermitType')} — {description}"
                if description
                else _span(page, "ctl00_PlaceHolderMain_lblPermitType")
            ),
            status_date="",
            portal_url=detail_url,
            details={"source": "live official portal (Accela Citizen Access)"},
        )


class AcaViewstateAdapter(CityAdapter):
    """source config: base_url, module (e.g. Permits).
    Flow: establish session -> GET search page -> POST form with viewstate ->
    parse the results grid (Date | Record Number | Status | Record Type |
    Project Name | Address)."""

    def lookup(self, permit_number: str) -> PermitRecord:
        source = self.jurisdiction["source"]
        base, module = source["base_url"], source.get("module", "Permits")
        permit_number = permit_number.strip().upper()
        search_url = f"{base}/Cap/CapHome.aspx?module={module}&TabName={module}"

        try:
            with httpx.Client(
                headers={"User-Agent": UA}, timeout=60, follow_redirects=True
            ) as client:
                client.get(f"{base}/Default.aspx")
                page = client.get(search_url).raise_for_status().text

                fields = {}
                for tag in re.findall(r"<input[^>]+>", page):
                    name = re.search(r'name="([^"]+)"', tag)
                    if not name:
                        continue
                    input_type = re.search(r'type="([^"]+)"', tag)
                    if input_type and input_type.group(1) in (
                        "submit", "button", "image", "checkbox", "radio",
                    ):
                        continue
                    value = re.search(r'value="([^"]*)"', tag)
                    fields[html_lib.unescape(name.group(1))] = (
                        html_lib.unescape(value.group(1)) if value else ""
                    )

                fields["ctl00$PlaceHolderMain$generalSearchForm$txtGSPermitNumber"] = permit_number
                fields["__EVENTTARGET"] = "ctl00$PlaceHolderMain$btnNewSearch"
                fields["__EVENTARGUMENT"] = ""

                result = client.post(
                    search_url, data=fields, headers={"Referer": search_url}
                ).raise_for_status().text
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(f"{self.jurisdiction['name']} portal unreachable: {exc}")

        if "gdvPermitList" not in result:
            raise PermitNotFound(permit_number)

        # data rows are flat (no nested tables) and class-tagged Odd/Even
        rows = []
        for tr in re.findall(
            r'<tr[^>]*class="[^"]*ACA_TabRow_(?:Odd|Even)[^"]*".*?</tr>', result, re.S
        ):
            cells = [_strip(td) for td in re.findall(r"<td[^>]*>.*?</td>", tr, re.S)]
            # leading hidden column(s) are empty — align on the date cell
            while cells and not re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
                cells.pop(0)
            if len(cells) >= 6:
                rows.append((cells, tr))
        if not rows:
            raise PermitNotFound(permit_number)

        # search is prefix-match; prefer the exact record, else first hit
        row, tr_html = next(
            (r for r in rows if r[0][1].upper() == permit_number), rows[0]
        )
        date, record_number, status, record_type, project_name, address = row[:6]

        detail_link = re.search(r'CapDetail\.aspx[^"\']*', tr_html)
        portal_url = (
            f"{base}/Cap/{html_lib.unescape(detail_link.group(0))}"
            if detail_link
            else search_url
        )

        return PermitRecord(
            permit_number=record_number,
            jurisdiction=self.jurisdiction["slug"],
            jurisdiction_name=self.jurisdiction["name"],
            status=status,
            address=address,
            description=f"{record_type} — {project_name}" if project_name else record_type,
            status_date=date,
            portal_url=portal_url,
            details={"source": "live official portal (Accela Citizen Access)"},
        )
