import asyncio

from stackchan_gateway import effects, tools


def test_pomodoro_focus_and_rest_cycle(monkeypatch):
    async def run():
        store = effects.TimerStore()
        messages = []
        monkeypatch.setattr(effects, "_notify", lambda title, message: messages.append(message))
        monkeypatch.setattr(effects, "_speak", lambda text: messages.append(text))
        timer = await store.add(label="写报告", duration_seconds=1, speak="专注结束", rest_seconds=1)
        await asyncio.sleep(1.2)
        assert (await store.list_active())[0]["phase"] == "rest"
        await asyncio.sleep(1.2)
        assert await store.list_active() == []
        assert "专注结束" in messages
        assert any("休息结束" in message for message in messages)
    asyncio.run(run())


def test_pomodoro_rejects_invalid_rest_duration():
    result = asyncio.run(tools.execute("computer_butler_pomodoro", {"rest_minutes": 0}))
    assert "error" in result


def test_pomodoro_defaults_and_cancel(monkeypatch):
    async def run():
        store = effects.TimerStore()
        monkeypatch.setattr(effects, "STORE", store)
        result = await tools.execute("computer_butler_pomodoro", {})
        assert result["minutes"] == 25
        assert result["rest_minutes"] == 5
        assert await store.cancel(result["timer_id"])
    asyncio.run(run())


def test_daily_brief_includes_weather_without_calendar(monkeypatch):
    monkeypatch.setattr(tools.mac_helpers, "weather", lambda location: {"location": location, "temp_C": "20"})
    result = asyncio.run(tools.execute("computer_butler_daily_brief", {"location": "Shanghai"}))
    assert result["weather"]["location"] == "Shanghai"
    assert "time" in result
    assert "calendar" not in result
