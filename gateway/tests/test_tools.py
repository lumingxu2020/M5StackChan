import asyncio

import pytest

from stackchan_gateway import tools


def test_registry_has_expected_categories():
    cats = {spec.category for spec in tools.all_tools()}
    assert cats == {"info", "control", "playful"}


def test_registry_includes_classics():
    names = {spec.name for spec in tools.all_tools()}
    for must in (
        "computer_butler_time",
        "computer_butler_status",
        "computer_butler_roll_dice",
        "computer_butler_joke",
        "computer_butler_pomodoro",
        "computer_butler_timer_set",
        "computer_butler_weather",
    ):
        assert must in names


def test_openai_tool_shape_for_roll_dice():
    spec = tools.get("computer_butler_roll_dice")
    payload = spec.to_openai_tool()
    assert payload["type"] == "function"
    assert payload["function"]["name"].startswith("computer_butler_")
    assert "properties" in payload["function"]["parameters"]


def test_execute_rolls_within_bounds():
    result = asyncio.run(tools.execute(
        "computer_butler_roll_dice",
        {"count": "3", "sides": "6"},
    ))
    assert len(result["rolls"]) == 3
    assert all(1 <= roll <= 6 for roll in result["rolls"])


def test_execute_math_evaluates_safely():
    result = asyncio.run(tools.execute("computer_butler_math", {"expression": "(3+5)*2"}))
    assert result == {"expression": "(3+5)*2", "result": 16}


def test_execute_math_rejects_import():
    result = asyncio.run(tools.execute(
        "computer_butler_math",
        {"expression": "__import__('os').system('echo pwned')"},
    ))
    assert "error" in result


def test_execute_unit_convert_temperature():
    result = asyncio.run(tools.execute(
        "computer_butler_unit_convert",
        {"value": "100", "from_unit": "c", "to_unit": "f"},
    ))
    assert result == {"value": 212.0, "from_unit": "c", "to_unit": "f", "kind": "temperature"}


def test_execute_pick_one_returns_one_item():
    result = asyncio.run(tools.execute(
        "computer_butler_pick_one",
        {"items": "火锅,日料,烤肉"},
    ))
    assert result["chosen"] in {"火锅", "日料", "烤肉"}


def test_execute_emoji_mood_has_pair():
    result = asyncio.run(tools.execute("computer_butler_emoji_mood", {}))
    assert set(result.keys()) == {"mood", "emoji"}
    assert result["emoji"]


def test_execute_countdown_days_until_future_date():
    result = asyncio.run(tools.execute(
        "computer_butler_countdown",
        {"date": "2099.01.01"},
    ))
    assert result["days"] > 0
    assert result["already_passed"] is False


def test_execute_unknown_tool_returns_error():
    result = asyncio.run(tools.execute("does_not_exist", {}))
    assert "error" in result


def test_execute_missing_required_argument():
    result = asyncio.run(tools.execute("computer_butler_math", {"expression": ""}))
    assert "error" in result


@pytest.mark.parametrize("expression", ["True+1", "1e309", "2**101"])
def test_execute_math_rejects_unsafe_numeric_inputs(expression):
    result = asyncio.run(
        tools.execute("computer_butler_math", {"expression": expression})
    )

    assert "error" in result


def test_timer_rejects_zero_seconds():
    result = asyncio.run(
        tools.execute("computer_butler_timer_set", {"seconds": 0})
    )

    assert "error" in result


def test_pick_one_requires_two_choices():
    result = asyncio.run(
        tools.execute("computer_butler_pick_one", {"items": "only"})
    )

    assert "error" in result


def test_execute_rejects_unexpected_arguments():
    result = asyncio.run(
        tools.execute("computer_butler_time", {"rogue": "value"})
    )

    assert "unexpected" in result["error"]


def test_custom_autonomous_tools_uses_exact_allowlist():
    selected = tools.autonomous_tools(
        "custom",
        {"computer_butler_time", "computer_butler_open_app"},
    )

    assert {spec.name for spec in selected} == {
        "computer_butler_time",
        "computer_butler_open_app",
    }
