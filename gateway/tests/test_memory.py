from stackchan_gateway import memory


def setup_function(_):
    memory.MEMORY._sessions.clear()


def test_history_round_trip():
    memory.MEMORY.append("a", "user", "hi")
    memory.MEMORY.append("a", "assistant", "hello")
    turns = memory.MEMORY.history("a")
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content == "hi"


def test_history_empty_for_unknown_session():
    assert memory.MEMORY.history("nope") == []


def test_forget_clears_history():
    memory.MEMORY.append("a", "user", "hi")
    assert memory.MEMORY.forget("a") is True
    assert memory.MEMORY.history("a") == []


def test_render_history_includes_labels():
    memory.MEMORY.append("a", "user", "hi")
    memory.MEMORY.append("a", "assistant", "hey")
    rendered = memory.render_history("a")
    assert "用户" in rendered and "机器人" in rendered


def test_render_history_empty_for_no_session():
    assert memory.render_history("") == ""
