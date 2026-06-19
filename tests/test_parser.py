"""Tests for src.discord_monitor.parse_gex_message()."""

from __future__ import annotations

import pytest

from src.discord_monitor import parse_gex_message


SAMPLE_VALID = (
    "2026-06-19 15:59:59     |     <https://gexbot.com>\n"
    "```\n"
    "| SPX Gamma                |          |\n"
    "|--------------------------|----------|\n"
    "| GEX by OI                |   -40.29 |\n"
    "| GEX by Volume            | -1246.41 |\n"
    "|                          |          |\n"
    "| Spot                     |  7497.86 |\n"
    "|                          |          |\n"
    "| Major Negative by Volume |  7500.00 |\n"
    "| Major Positive by Volume |  7505.83 |\n"
    "| Major Negative by OI     |  7500.00 |\n"
    "| Major Positive by OI     |  7700.00 |\n"
    "| Zero Gamma               |  7504.74 |\n"
    "```"
)


@pytest.mark.parametrize(
    "msg, expected",
    [
        (
            SAMPLE_VALID,
            {
                "timestamp": "2026-06-19 15:59:59",
                "gex_by_oi": -40.29,
                "gex_by_volume": -1246.41,
                "spot": 7497.86,
                "major_negative_by_volume": 7500.00,
                "major_positive_by_volume": 7505.83,
                "major_negative_by_oi": 7500.00,
                "major_positive_by_oi": 7700.00,
                "zero_gamma": 7504.74,
            },
        ),
        (
            # Different timestamp + values, same shape.
            "2025-01-02 09:30:00 | gex\n"
            "```\n"
            "| GEX by OI                |   100.50 |\n"
            "| GEX by Volume            |   200.00 |\n"
            "| Spot                     |  5000.00 |\n"
            "| Zero Gamma               |  4999.00 |\n"
            "```",
            {
                "timestamp": "2025-01-02 09:30:00",
                "gex_by_oi": 100.50,
                "gex_by_volume": 200.00,
                "spot": 5000.00,
                "zero_gamma": 4999.00,
            },
        ),
        (
            # Sparse — only Spot and Zero Gamma present.
            "2026-12-31 16:00:00 | gex\n"
            "```\n"
            "| Spot                     |  6000.00 |\n"
            "| Zero Gamma               |  5995.00 |\n"
            "```",
            {
                "timestamp": "2026-12-31 16:00:00",
                "spot": 6000.00,
                "zero_gamma": 5995.00,
            },
        ),
    ],
)
def test_parse_gex_message_returns_dict_with_expected_fields(msg, expected):
    parsed = parse_gex_message(msg)
    assert parsed is not None, "parser should return dict for valid input"
    assert parsed["timestamp"] == expected["timestamp"]
    for key, value in expected.items():
        if key == "timestamp":
            continue
        assert parsed[key] == pytest.approx(value), (
            f"field {key}: expected {value}, got {parsed.get(key)}"
        )


def test_parse_gex_message_returns_none_without_timestamp():
    msg = (
        "no timestamp here\n"
        "```\n"
        "| Spot |  5000.00 |\n"
        "```"
    )
    assert parse_gex_message(msg) is None


def test_parse_gex_message_returns_none_without_any_field():
    msg = "2026-06-19 15:59:59 | just a timestamp, no fields"
    assert parse_gex_message(msg) is None


def test_parse_gex_message_handles_negative_values():
    msg = (
        "2026-06-19 15:59:59 | gex\n"
        "```\n"
        "| Spot                     |  -12.34 |\n"
        "| Zero Gamma               |  -99.99 |\n"
        "```"
    )
    parsed = parse_gex_message(msg)
    assert parsed is not None
    assert parsed["spot"] == pytest.approx(-12.34)
    assert parsed["zero_gamma"] == pytest.approx(-99.99)
