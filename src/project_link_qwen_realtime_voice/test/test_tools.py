from project_link_qwen_realtime_voice.tools import (
    is_explicit_confirmation,
    is_explicit_exit,
    normalize_spoken_text,
    tool_schemas,
)


def test_confirmation_requires_whole_explicit_phrase():
    assert is_explicit_confirmation("确认开始。")
    assert not is_explicit_confirmation("我还没有确认开始")
    assert not is_explicit_confirmation("先别开始")


def test_exit_keywords_are_local_and_exact():
    assert is_explicit_exit("停止！")
    assert is_explicit_exit("退出对话")
    assert not is_explicit_exit("不要停止播放音乐")


def test_normalize_spoken_text_removes_spacing_and_punctuation():
    assert normalize_spoken_text(" 确认，开始！") == "确认开始"


def test_demo_tool_is_exposed_only_in_demo_mode():
    production = {entry["function"]["name"] for entry in tool_schemas(False)}
    demo = {entry["function"]["name"] for entry in tool_schemas(True)}
    assert "demo_motion" not in production
    assert "demo_motion" in demo
