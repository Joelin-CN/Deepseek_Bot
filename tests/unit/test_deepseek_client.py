"""DeepSeek HTTP 适配器测试（httpx.MockTransport 替身服务）。"""

import json

import httpx
import pytest

from companion.adapters.deepseek import DeepSeekClient, DeepSeekError
from companion.config.settings import DeepSeekSettings


def make_client(handler) -> DeepSeekClient:
    settings = DeepSeekSettings(base_url="https://api.deepseek.com", model="deepseek-flash", timeout=5)
    return DeepSeekClient(settings, "sk-test", transport=httpx.MockTransport(handler))


def ok_response(content: str = "你好！") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "deepseek-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 16, "completion_tokens": 10, "total_tokens": 26},
        },
    )


async def test_chat_success_parses_content_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return ok_response("你好！有什么可以帮你？")

    result = await make_client(handler).chat([{"role": "user", "content": "你好"}])
    assert seen["path"] == "/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "deepseek-flash"
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert result.content == "你好！有什么可以帮你？"
    assert result.prompt_tokens == 16
    assert result.completion_tokens == 10
    assert result.total_tokens == 26


async def test_enable_thinking_sent_when_configured():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return ok_response()

    settings = DeepSeekSettings(enable_thinking=True)
    await DeepSeekClient(settings, "sk-test", transport=httpx.MockTransport(handler)).chat(
        [{"role": "user", "content": "hi"}]
    )
    assert seen["body"]["thinking"] == {"type": "enabled"}


@pytest.mark.parametrize(
    ("status", "kind", "hint"),
    [
        (401, "auth", "DEEPSEEK_API_KEY"),
        (402, "balance", "余额不足"),
        (429, "rate_limit", "频繁"),
        (500, "server", "不可用"),
        (503, "server", "不可用"),
    ],
)
async def test_error_status_maps_to_kind_and_user_message(status, kind, hint):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    with pytest.raises(DeepSeekError) as excinfo:
        await make_client(handler).chat([{"role": "user", "content": "hi"}])
    assert excinfo.value.kind == kind
    assert excinfo.value.status_code == status
    assert hint in excinfo.value.user_message


async def test_timeout_maps_to_timeout_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(DeepSeekError) as excinfo:
        await make_client(handler).chat([{"role": "user", "content": "hi"}])
    assert excinfo.value.kind == "timeout"
    assert "超时" in excinfo.value.user_message


async def test_malformed_200_body_maps_to_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(DeepSeekError) as excinfo:
        await make_client(handler).chat([{"role": "user", "content": "hi"}])
    assert excinfo.value.kind == "unknown"
