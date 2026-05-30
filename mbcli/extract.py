"""Parse the Mercedes "me" API responses into a structured vehicle status.

The relevant endpoints (on api.oneweb.mercedes-benz.com), discovered live:
  /me/vsc/v1/user/vehicles            -> items[] with vehicleName, vin, order
  /me/vsc/v1/user/vehicle/status-information
                                      -> liveData.{mileage, levels, ranges,
                                         tires, ...} for the primary vehicle

`flatten` is also kept for the `discover` command, which explores raw JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts/lists into {dotted.path: leaf_value}."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, child))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    elif prefix:
        out[prefix] = obj
    return out


def flatten_responses(responses: list[Any]) -> dict[str, Any]:
    """Flatten every response body into one {path: value} map (for discover)."""
    flat: dict[str, Any] = {}
    for resp in responses:
        flat.update(flatten(getattr(resp, "json", resp)))
    return flat


# How the API spells units -> how we display them.
_UNIT_DISPLAY = {
    "PERCENTAGE": "%", "PERCENT": "%",
    "MILES": "mi", "KILOMETERS": "km", "KM": "km",
    "PSI": "PSI", "KPA": "kPa", "BAR": "bar",
}


def display_unit(unit: str | None) -> str:
    if not unit:
        return ""
    return _UNIT_DISPLAY.get(unit.upper(), unit)


@dataclass
class Vehicle:
    name: str | None = None
    vin: str | None = None
    fin: str | None = None
    model: str | None = None
    order: int = 999

    @property
    def selector(self) -> str | None:
        """Value for the `x-me-finorvin` header used to select this vehicle."""
        return self.vin or self.fin

    def as_dict(self) -> dict:
        return {"name": self.name, "vin": self.vin, "fin": self.fin,
                "model": self.model, "order": self.order}


@dataclass
class VehicleStatus:
    vehicle: Vehicle = field(default_factory=Vehicle)
    soc: tuple | None = None            # (value, unit) state of charge
    electric_range: tuple | None = None
    fuel_level: tuple | None = None
    fuel_range: tuple | None = None
    mileage: tuple | None = None
    tires: list[dict] = field(default_factory=list)  # {position,value,unit,warning}
    last_update: str | None = None
    found: bool = False                 # did we locate live status data?


def _bodies(responses: list[Any], url_substr: str) -> list[Any]:
    out = []
    for r in responses:
        url = getattr(r, "url", "") or ""
        if url_substr in url:
            out.append(getattr(r, "json", None))
    return out


def parse_vehicles(vehicles_json: Any) -> list[Vehicle]:
    """Parse a `/user/vehicles` body into Vehicles, sorted by display order."""
    if not isinstance(vehicles_json, dict):
        return []
    items = [i for i in vehicles_json.get("items", [])
             if isinstance(i, dict) and i.get("vehicleName")]
    items.sort(key=lambda i: i.get("order", 999))
    return [
        Vehicle(name=i.get("vehicleName"), vin=i.get("vin"), fin=i.get("fin"),
                model=i.get("modelDescription"), order=i.get("order", 999))
        for i in items
    ]


def vehicles_from_responses(responses: list[Any]) -> list[Vehicle]:
    """Extract the vehicle list from captured browser responses."""
    for j in _bodies(responses, "/user/vehicles"):
        vehicles = parse_vehicles(j)
        if vehicles:
            return vehicles
    return []


def find_status_json(responses: list[Any]) -> Any:
    """First `status-information` body among captured browser responses."""
    for j in _bodies(responses, "status-information"):
        if isinstance(j, dict):
            return j
    return None


def parse_live_data(status_json: Any, vehicle: Vehicle | None = None) -> VehicleStatus:
    """Build a VehicleStatus from a `status-information` body for one vehicle."""
    st = VehicleStatus(vehicle=vehicle or Vehicle())
    ld = status_json.get("liveData") if isinstance(status_json, dict) else None
    if not isinstance(ld, dict):
        return st
    st.found = True
    st.last_update = ld.get("lastUpdateTimestamp")

    m = ld.get("mileage")
    if isinstance(m, dict):
        st.mileage = (m.get("value"), m.get("unit"))

    for lvl in ld.get("levels") or []:
        if not isinstance(lvl, dict):
            continue
        pair = (lvl.get("value"), lvl.get("unit"))
        if (lvl.get("type") or "").upper() == "ELECTRIC":
            st.soc = pair
        else:  # FUEL / GASOLINE / DIESEL ...
            st.fuel_level = pair

    for rg in ld.get("ranges") or []:
        if not isinstance(rg, dict):
            continue
        pair = (rg.get("value"), rg.get("unit"))
        if (rg.get("type") or "").upper() == "ELECTRIC":
            st.electric_range = pair
        else:
            st.fuel_range = pair

    st.tires = [
        {"position": t.get("type"), "value": t.get("value"),
         "unit": t.get("unit"), "warning": t.get("warning")}
        for t in (ld.get("tires") or []) if isinstance(t, dict)
    ]
    return st
