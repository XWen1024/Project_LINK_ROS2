import uuid

import pytest

from project_link_fall_response.gateway_contract import ContractError, validate_event


def valid(mode="real"):
    return {
        "event_id": str(uuid.uuid4()),
        "mode": mode,
        "occurred_at_ms": 1787131200000,
        "device_name": " phone ",
        "cancel_window_ms": 15000,
        "imu": None if mode == "demo" else {
            "peak_accel_g": 2.8,
            "orientation_change_deg": 63.0,
            "inactivity_ms": 2200,
        },
    }


def test_validates_real_and_trims_device_name():
    payload = validate_event(valid())
    assert payload["device_name"] == "phone"
    assert payload["imu"]["inactivity_ms"] == 2200


def test_demo_requires_null_imu():
    payload = valid("demo")
    payload["imu"] = {"peak_accel_g": 1}
    with pytest.raises(ContractError):
        validate_event(payload)


@pytest.mark.parametrize("field,value", [("cancel_window_ms", 10000), ("mode", "test"), ("device_name", "")])
def test_rejects_invalid_contract(field, value):
    payload = valid()
    payload[field] = value
    with pytest.raises(ContractError):
        validate_event(payload)
