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
            if cleaned in j.get("zips", ()) or (
                "zip_prefix" in j and cleaned.startswith(j["zip_prefix"])
            ):
                return j
        raise UnsupportedLocation(text)

    for j in JURISDICTIONS:
        if cleaned == j["slug"] or cleaned.split(",")[0].strip() == j["city"].lower():
            return j
    raise UnsupportedLocation(text)
