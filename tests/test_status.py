"""Tests for the precise Mercedes status parser and renderer."""

from mbcli.extract import parse_live_data, parse_vehicles, vehicles_from_responses
from mbcli.render import render_statuses, render_vehicle_status, vehicle_kind


class _Resp:
    def __init__(self, url, body):
        self.url = url
        self.json = body


VEHICLES_BODY = {"items": [
    {"vehicleName": "GLC 300", "vin": "VINGLC", "fin": "FINGLC",
     "modelDescription": "GLC 300", "order": 1},
    {"vehicleName": "EQB 300", "vin": "VINEQB", "fin": "FINEQB",
     "modelDescription": "MY24 EQB300W4", "order": 0},
]}

EQB_STATUS = {"liveData": {
    "lastUpdateTimestamp": 1780086509985,
    "levels": [{"value": 62, "type": "ELECTRIC", "unit": "PERCENTAGE"}],
    "mileage": {"value": 15126, "unit": "MILES"},
    "ranges": [{"value": 146, "type": "ELECTRIC", "unit": "MILES"}],
    "tires": [
        {"value": 47.5, "type": "FRONT_LEFT", "warning": "OK", "unit": "PSI"},
        {"value": 47.9, "type": "FRONT_RIGHT", "warning": "OK", "unit": "PSI"},
        {"value": 47.9, "type": "REAR_LEFT", "warning": "OK", "unit": "PSI"},
        {"value": 46.0, "type": "REAR_RIGHT", "warning": "OK", "unit": "PSI"},
    ],
}}

GLC_STATUS = {"liveData": {
    "levels": [{"value": 59, "type": "FUEL", "unit": "PERCENTAGE"}],
    "ranges": [{"value": 205, "type": "FUEL", "unit": "MILES"}],
    "mileage": {"value": 53307, "unit": "MILES"},
}}


def test_parse_vehicles_sorted_by_order():
    vehicles = parse_vehicles(VEHICLES_BODY)
    assert [v.name for v in vehicles] == ["EQB 300", "GLC 300"]  # order 0 first
    assert vehicles[0].vin == "VINEQB"
    assert vehicles[0].selector == "VINEQB"  # vin preferred for x-me-finorvin


def test_vehicles_from_responses():
    resp = _Resp("https://api/me/vsc/v1/user/vehicles?&locale=en-US", VEHICLES_BODY)
    vehicles = vehicles_from_responses([resp])
    assert [v.name for v in vehicles] == ["EQB 300", "GLC 300"]


def test_parse_live_data_electric():
    eqb = parse_vehicles(VEHICLES_BODY)[0]
    st = parse_live_data(EQB_STATUS, eqb)
    assert st.found
    assert st.vehicle.name == "EQB 300"
    assert st.soc == (62, "PERCENTAGE")
    assert st.electric_range == (146, "MILES")
    assert st.mileage == (15126, "MILES")
    assert st.fuel_level is None
    assert len(st.tires) == 4 and st.tires[0]["position"] == "FRONT_LEFT"


def test_parse_live_data_gas_maps_fuel_not_soc():
    glc = parse_vehicles(VEHICLES_BODY)[1]
    st = parse_live_data(GLC_STATUS, glc)
    assert st.soc is None
    assert st.fuel_level == (59, "PERCENTAGE")
    assert st.fuel_range == (205, "MILES")
    assert st.mileage == (53307, "MILES")


def test_render_both_vehicles():
    vehicles = parse_vehicles(VEHICLES_BODY)
    statuses = [parse_live_data(EQB_STATUS, vehicles[0]),
                parse_live_data(GLC_STATUS, vehicles[1])]
    out = render_statuses(statuses)
    assert "EQB 300  (VINEQB)" in out
    assert "GLC 300  (VINGLC)" in out
    assert "State of charge : 62%" in out
    assert "Fuel level      : 59%" in out
    assert out.count("Odometer") == 2


def test_hide_replaces_identity_with_kind():
    vehicles = parse_vehicles(VEHICLES_BODY)
    eqb = parse_live_data(EQB_STATUS, vehicles[0])
    glc = parse_live_data(GLC_STATUS, vehicles[1])
    assert vehicle_kind(eqb) == "Electric Vehicle"
    assert vehicle_kind(glc) == "Gas Vehicle"
    out = render_statuses([eqb, glc], hide=True)
    assert "Electric Vehicle" in out and "Gas Vehicle" in out
    assert "VINEQB" not in out and "EQB 300" not in out  # identity hidden
