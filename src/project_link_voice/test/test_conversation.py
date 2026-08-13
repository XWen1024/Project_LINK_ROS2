from project_link_voice.conversation import (
    DEFAULT_EXIT_KEYWORDS,
    conversation_limit_reason,
    is_conversation_exit,
    normalize_spoken_text,
)


def test_exit_keywords_ignore_spacing_and_punctuation():
    assert is_conversation_exit("好，退 下 吧！", DEFAULT_EXIT_KEYWORDS)
    assert is_conversation_exit("不用了，谢谢", DEFAULT_EXIT_KEYWORDS)
    assert is_conversation_exit("停一下", DEFAULT_EXIT_KEYWORDS)


def test_normal_chat_does_not_exit_conversation():
    assert not is_conversation_exit("继续给我介绍一下这个功能", DEFAULT_EXIT_KEYWORDS)
    assert not is_conversation_exit("停止距离是多少", ("停止运行",))


def test_normalize_spoken_text_keeps_chinese_and_alphanumerics():
    assert normalize_spoken_text(" Exit  对话！ ") == "exit对话"


def test_conversation_limits_are_bounded():
    assert conversation_limit_reason(20, 10.0, 20, 300.0) == "max_turns"
    assert conversation_limit_reason(2, 300.0, 20, 300.0) == "max_session"
    assert conversation_limit_reason(2, 20.0, 20, 300.0) is None


def test_zero_limits_disable_the_corresponding_bound():
    assert conversation_limit_reason(999, 999.0, 0, 0.0) is None
