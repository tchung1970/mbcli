"""Human-readable rendering of a parsed VehicleStatus."""

from __future__ import annotations

from datetime import datetime

from .extract import VehicleStatus, display_unit


def _fmt(pair) -> str:
    if not pair or pair[0] is None:
        return "—"
    value, unit = pair
    u = display_unit(unit)
    if u == "%":
        return f"{value}%"
    return f"{value} {u}".strip()


def vehicle_kind(st: VehicleStatus) -> str:
    """Generic powertrain label, inferred from which metrics are present."""
    electric = st.soc is not None or st.electric_range is not None
    fuel = st.fuel_level is not None or st.fuel_range is not None
    if electric and fuel:
        return "Hybrid Vehicle"
    if electric:
        return "Electric Vehicle"
    if fuel:
        return "Gas Vehicle"
    return "Vehicle"


def render_statuses(statuses: list[VehicleStatus], *, hide: bool = False) -> str:
    """Render one or more vehicles, separated by a blank line."""
    if not statuses:
        return "No vehicles found."
    return "\n\n".join(render_vehicle_status(st, hide=hide) for st in statuses)


def render_vehicle_status(st: VehicleStatus, *, hide: bool = False) -> str:
    v = st.vehicle
    if hide:
        title = vehicle_kind(st)
    else:
        title = v.name or v.model or "Vehicle"
        if v.vin:
            title = f"{title}  ({v.vin})"
    lines = [title]

    if not st.found:
        lines.append("  (no live status data found — run `mbcli discover` to inspect)")
        return "\n".join(lines)

    if st.soc:
        lines.append(f"  State of charge : {_fmt(st.soc)}")
    if st.electric_range:
        lines.append(f"  Electric range  : {_fmt(st.electric_range)}")
    if st.fuel_level:
        lines.append(f"  Fuel level      : {_fmt(st.fuel_level)}")
    if st.fuel_range:
        lines.append(f"  Fuel range      : {_fmt(st.fuel_range)}")
    lines.append(f"  Odometer        : {_fmt(st.mileage)}")

    if st.tires:
        lines.append("  Tire pressure:")
        for t in st.tires:
            pos = (t.get("position") or "").replace("_", " ").title()
            warn = "" if t.get("warning") in (None, "OK") else f"  [{t['warning']}]"
            lines.append(f"    {pos:<12} {_fmt((t.get('value'), t.get('unit')))}{warn}")

    if st.last_update:
        lines.append(f"  Updated         : {_fmt_timestamp(st.last_update)}")
    return "\n".join(lines)


def _fmt_timestamp(ts) -> str:
    """Format an epoch timestamp (seconds or milliseconds) as local time."""
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    if n > 1e12:  # milliseconds
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n).strftime("%Y-%m-%d %I:%M %p")
    except (OverflowError, OSError, ValueError):
        return str(ts)
