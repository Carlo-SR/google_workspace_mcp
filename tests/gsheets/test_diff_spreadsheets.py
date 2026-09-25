"""Tests for formula-level spreadsheet diffing.

The interesting cases are positional: ``spreadsheets.values.get`` trims its
response to the populated bounding box, so two spreadsheets can return arrays
whose [0][0] refers to different cells.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from gsheets.sheets_tools import (
    _a1_address,
    _cells_by_absolute_coordinate,
    _parse_a1_range_origin,
    read_sheet_values,
)


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(response_a, response_b):
    """Create a Sheets service mock returning A then B for values.get."""
    mock_service = Mock()
    mock_service.spreadsheets().values().get().execute = Mock(
        side_effect=[response_a, response_b]
    )
    return mock_service


async def _call_diff(service, range_name="Sheet1!A1:Z1000"):
    """The diff is reached through read_sheet_values, not a separate tool."""
    return await _unwrap(read_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet-a",
        compare_with_spreadsheet_id="sheet-b",
        range_name=range_name,
    )


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "range_str,expected",
    [
        ("Sheet1!A1:C3", (0, 0)),
        ("Sheet1!B2:D7", (1, 1)),
        ("'My Sheet'!C5:C9", (4, 2)),
        ("'Weird!Name'!B3:B4", (2, 1)),
        ("Sheet1!AA10:AB12", (9, 26)),
        ("Sheet1!$B$2:$D$7", (1, 1)),
        ("B2:D7", (1, 1)),
        ("Sheet1", (0, 0)),
        ("", (0, 0)),
    ],
)
def test_parse_a1_range_origin(range_str, expected):
    assert _parse_a1_range_origin(range_str) == expected


def test_a1_address_round_trips_column_letters():
    assert _a1_address(0, 0) == "A1"
    assert _a1_address(1, 1) == "B2"
    assert _a1_address(9, 26) == "AA10"


def test_cells_by_absolute_coordinate_applies_origin_and_skips_blanks():
    cells = _cells_by_absolute_coordinate([["x", ""], [None, "y"]], (1, 1))
    assert cells == {(1, 1): "x", (2, 2): "y"}


# ----------------------------------------------------------------- positional safety


@pytest.mark.asyncio
async def test_diff_uses_absolute_addresses_for_offset_ranges():
    """An offset range must not be reported as if it started at A1."""
    service = _create_mock_service(
        {"range": "Sheet1!B2:B2", "values": [["=SUM(A1:A5)"]]},
        {"range": "Sheet1!B2:B2", "values": [["15"]]},
    )

    result = await _call_diff(service, range_name="Sheet1!B2:B2")

    assert "- frozen (formula -> value in B): 1" in result
    assert "B2: =SUM(A1:A5) -> 15" in result
    # The A1 substring inside the formula text must not be mistaken for an address.
    assert "\n    A1: " not in result


@pytest.mark.asyncio
async def test_diff_aligns_grids_with_different_bounding_boxes():
    """Different leading empty cells must not shift the comparison."""
    # A is populated from A1, B only from B2. The single shared cell is B2.
    service = _create_mock_service(
        {"range": "Sheet1!A1:B2", "values": [["=A1+1", ""], ["", "=B2+1"]]},
        {"range": "Sheet1!B2:B2", "values": [["=B2+1"]]},
    )

    result = await _call_diff(service)

    # B2 is identical in both, A1 exists only in A and must count as cleared.
    assert "- unchanged formulas: 1" in result
    assert "- cleared in B: 1" in result
    assert "A1: =A1+1" in result
    assert "- changed formulas: 0" in result


@pytest.mark.asyncio
async def test_diff_reports_changed_literals_separately():
    """A literal 1 -> 2 is a change, not an unchanged literal."""
    service = _create_mock_service(
        {"range": "Sheet1!A1:A2", "values": [["1"], ["keep"]]},
        {"range": "Sheet1!A1:A2", "values": [["2"], ["keep"]]},
    )

    result = await _call_diff(service)

    assert "- changed literals: 1" in result
    assert "- unchanged formulas: 0 | unchanged literals: 1" in result
    assert "A1: 1 -> 2" in result


@pytest.mark.asyncio
async def test_diff_counts_cleared_and_added_by_absolute_position():
    service = _create_mock_service(
        {"range": "Sheet1!A1:A1", "values": [["only-in-a"]]},
        {"range": "Sheet1!C3:C3", "values": [["only-in-b"]]},
    )

    result = await _call_diff(service)

    assert "- cleared in B: 1" in result
    assert "- added in B: 1" in result
    assert "A1: only-in-a" in result
    assert "C3: only-in-b" in result


@pytest.mark.asyncio
async def test_diff_reports_value_replaced_by_formula_as_thawed():
    """A literal replaced by a formula is a formula change, not a literal one."""
    service = _create_mock_service(
        {"range": "Sheet1!A1:A1", "values": [["42"]]},
        {"range": "Sheet1!A1:A1", "values": [["=SUM(B1:B9)"]]},
    )

    result = await _call_diff(service)

    assert "- thawed (value -> formula in B): 1" in result
    assert "- changed literals: 0" in result
    assert "A1: 42 -> =SUM(B1:B9)" in result


@pytest.mark.asyncio
async def test_diff_frozen_and_thawed_are_symmetric():
    service = _create_mock_service(
        {"range": "Sheet1!A1:A2", "values": [["=A9"], ["7"]]},
        {"range": "Sheet1!A1:A2", "values": [["7"], ["=A9"]]},
    )

    result = await _call_diff(service)

    assert "- frozen (formula -> value in B): 1" in result
    assert "- thawed (value -> formula in B): 1" in result
    assert "- changed literals: 0" in result


# ------------------------------------------------- no new tools on the surface


def _tool_names_in_tiers(section):
    """Names exposed for a service, per core/tool_tiers.yaml.

    core/tool_registry.py:filter_server_tools prunes anything absent from this
    file at startup, so the yaml is what actually determines the tool list a
    client sees. Reading it keeps this test independent of the shared server
    registry, which other tests in the suite filter in place.
    """
    import os

    import yaml

    yaml_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "core", "tool_tiers.yaml"
    )
    with open(yaml_path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    names = set()
    for tier in ("core", "extended", "complete"):
        names.update(config.get(section, {}).get(tier) or [])
    return names


def test_diff_and_audit_add_no_new_tools():
    """Both must stay parameters of existing tools, not tools of their own."""
    import inspect

    from gsheets.sheets_tools import get_spreadsheet_info

    exposed = _tool_names_in_tiers("sheets")
    assert {"read_sheet_values", "get_spreadsheet_info"} <= exposed, (
        "Detection is broken: the host tools themselves are not listed."
    )
    assert "diff_spreadsheets" not in exposed
    assert "audit_spreadsheet_errors" not in exposed

    assert (
        "compare_with_spreadsheet_id"
        in inspect.signature(_unwrap(read_sheet_values)).parameters
    )
    assert (
        "include_error_audit"
        in inspect.signature(_unwrap(get_spreadsheet_info)).parameters
    )


@pytest.mark.asyncio
async def test_get_spreadsheet_info_appends_error_audit():
    from gsheets.sheets_tools import get_spreadsheet_info

    service = Mock()
    service.spreadsheets().get().execute = Mock(
        return_value={
            "spreadsheetId": "sheet-a",
            "properties": {"title": "Book", "locale": "en_US"},
            "sheets": [
                {
                    "properties": {
                        "title": "Sheet1",
                        "sheetId": 0,
                        "gridProperties": {"rowCount": 10, "columnCount": 5},
                    }
                }
            ],
        }
    )

    with patch(
        "gsheets.sheets_tools._audit_spreadsheet_errors_impl",
        AsyncMock(return_value="Formula errors: 3 in Sheet1"),
    ) as audit:
        result = await _unwrap(get_spreadsheet_info)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-a",
            include_error_audit=True,
        )

    audit.assert_awaited_once()
    assert "Book" in result
    assert "Formula errors: 3 in Sheet1" in result


@pytest.mark.asyncio
async def test_get_spreadsheet_info_skips_audit_by_default():
    from gsheets.sheets_tools import get_spreadsheet_info

    service = Mock()
    service.spreadsheets().get().execute = Mock(
        return_value={
            "spreadsheetId": "sheet-a",
            "properties": {"title": "Book", "locale": "en_US"},
            "sheets": [],
        }
    )

    with patch("gsheets.sheets_tools._audit_spreadsheet_errors_impl") as audit:
        result = await _unwrap(get_spreadsheet_info)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-a",
        )

    audit.assert_not_called()
    assert "Book" in result
