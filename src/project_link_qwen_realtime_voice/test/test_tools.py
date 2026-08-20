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


def test_exit_keywords_accept_explicit_natural_commands():
    assert is_explicit_exit("停止！")
    assert is_explicit_exit("退出对话")
    assert is_explicit_exit("好了，你退下吧")
    assert is_explicit_exit("请停止一下")
    assert is_explicit_exit("退出退出")
    assert is_explicit_exit("关闭")
    assert is_explicit_exit("关闭语音服务")
    assert not is_explicit_exit("不要停止播放音乐")
    assert not is_explicit_exit("取消导航到客厅")
    assert not is_explicit_exit("关闭警灯")


def test_normalize_spoken_text_removes_spacing_and_punctuation():
    assert normalize_spoken_text(" 确认，开始！") == "确认开始"


def test_demo_tool_is_exposed_only_in_demo_mode():
    production = {entry["function"]["name"] for entry in tool_schemas(False)}
    demo = {entry["function"]["name"] for entry in tool_schemas(True)}
    assert "end_conversation" in production
    assert "demo_motion" not in production
    assert "demo_motion" in demo
