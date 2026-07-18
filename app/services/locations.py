"""Resolve user input — a city name, ZIP code, or slug — to a jurisdiction."""

import re

from app.registry import JURISDICTIONS


class UnsupportedLocation(Exception):
    def __init__(self, text: str):
        supported = ", ".join(j["city"] for j in JURISDICTIONS)
        super().__init__(
            f"'{text}' is not a supported city or ZIP yet. Supported: {supported}."
        )


def resolve(text: str) -> dict:
    cleaned = text.strip().lower()
    if not cleaned:
        raise UnsupportedLocation(text)

    if re.fullmatch(r"\d{5}", cleaned):
        for j in JURISDICTIONS:
            if cleaned in j.get("zips", ()) or any(
                cleaned.startswith(p) for p in j.get("zip_prefixes", ())
            ):
                return j
        raise UnsupportedLocation(text)

    city_part = cleaned.split(",")[0].strip()
    for j in JURISDICTIONS:
        if (
            cleaned == j["slug"]
            or city_part == j["city"].lower()
            or city_part in j.get("aliases", ())
        ):
            return j
    raise UnsupportedLocation(text)
