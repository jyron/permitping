from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PermitRecord:
    permit_number: str
    jurisdiction: str
    jurisdiction_name: str
    status: str
    address: str = ""
    description: str = ""
    status_date: str = ""
    portal_url: str = ""
    details: dict = field(default_factory=dict)


class PermitNotFound(Exception):
    pass


class AdapterUnavailable(Exception):
    pass


class CityAdapter(ABC):
    """One adapter per municipal portal. Implement lookup(); everything else
    (caching, monitoring, email) is handled above this layer."""

    def __init__(self, jurisdiction: dict):
        self.jurisdiction = jurisdiction

    @abstractmethod
    def lookup(self, permit_number: str) -> PermitRecord:
        ...
