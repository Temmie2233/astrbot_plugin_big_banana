from types import SimpleNamespace

from core.providers.utils import extract_upstream_error_message


def test_unwraps_upstream_error_json() -> None:
    exc = SimpleNamespace(
        body={
            "error": {
                "message": (
                    'Upstream error: {"code":"content_policy_violation",'
                    '"error":"非常抱歉，该提示可能违反了关于裸露、色情或情色内容的'
                    '防护限制。","reason":"content_policy_violation"}'
                ),
                "type": "api_error",
            }
        }
    )

    message = extract_upstream_error_message(exc)

    assert message == "非常抱歉，该提示可能违反了关于裸露、色情或情色内容的防护限制。"


def test_reads_message_from_dict_body() -> None:
    body = {"error": {"message": "普通错误"}}

    assert extract_upstream_error_message(body) == "普通错误"


def test_keeps_plain_string() -> None:
    assert extract_upstream_error_message(" 出错了 ") == "出错了"


def test_keeps_unparsable_upstream_prefix() -> None:
    assert (
        extract_upstream_error_message("Upstream error: not json")
        == "Upstream error: not json"
    )


def test_returns_none_for_empty() -> None:
    assert extract_upstream_error_message(None) is None
    assert extract_upstream_error_message("") is None
    assert extract_upstream_error_message(SimpleNamespace(body=None)) is None
