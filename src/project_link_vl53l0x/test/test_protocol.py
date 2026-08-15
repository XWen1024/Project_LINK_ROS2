import pytest

from project_link_vl53l0x.protocol import DataFrame, ProtocolError, parse_data_line


def test_valid_data_line():
    assert parse_data_line("DATA,42,2150,687,0\r\n") == DataFrame(42, 2150, 687, 0)


def test_non_data_line_is_ignored():
    assert parse_data_line("# VL53L0X_USB_BRIDGE,1") is None
    assert parse_data_line("ERROR,0,I2C_SETUP,ESP_ERR_NOT_FOUND") is None


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("DATA,1,2,3", "bad_field_count"),
        ("DATA,one,2,3,0", "bad_integer"),
        ("DATA,1,2,-3,0", "negative_field"),
    ],
)
def test_invalid_data_line(line, reason):
    with pytest.raises(ProtocolError, match=reason):
        parse_data_line(line)
