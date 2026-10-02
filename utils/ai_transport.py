"""Provider-aware JSON transport separated from research prompts and schemas."""
from __future__ import annotations
import json
from typing import Any
from urllib.request import Request
from urllib.error import HTTPError, URLError


def request_json(config: dict[str, Any], api_key: str, system: str, payload: dict[str, Any], max_tokens: int, *, urlopen, parse_content, parse_object, DeepSeekRequestError) -> dict[str, Any]:
    """Call the configured compatible endpoint with capability-aware fields."""
    base_url = str(config["base_url"]).rstrip("/")
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system + " 输出必须是一个 JSON 对象，不要使用 Markdown 代码块或额外说明。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    from urllib.parse import urlsplit
    from PySide6.QtCore import QThread

    host = (urlsplit(base_url).hostname or "").casefold()
    capabilities = config.get("capabilities", {})
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    if capabilities.get("json_mode", host in {"api.deepseek.com", "api.openai.com"}):
        body["response_format"] = {"type": "json_object"}
    if capabilities.get("thinking", host == "api.deepseek.com"):
        body["thinking"] = {"type": "disabled"}
    last_issue = ""
    for attempt in range(2):
        if QThread.currentThread().isInterruptionRequested():
            raise InterruptedError("AI 任务已暂停")
        request_body = dict(body)
        request_body["messages"] = list(body["messages"])
        if attempt:
            request_body["messages"].append(
                {
                    "role": "user",
                    "content": "上一次输出无法读取。请严格只返回符合前述 schema 的单一 JSON 对象。",
                }
            )
        encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        request = Request(
            endpoint,
            data=encoded,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ResearchAssistant",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310 - endpoint is explicitly configured by the user
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            raise DeepSeekRequestError(
                f"AI 服务请求失败（HTTP {error.code}）。请检查密钥、模型名称、余额或服务状态。"
            ) from error
        except URLError as error:
            raise DeepSeekRequestError("无法连接 AI 服务，请检查网络、API 地址或代理设置。") from error
        except TimeoutError as error:
            raise DeepSeekRequestError("AI 服务响应超时，请稍后重试。") from error
        if QThread.currentThread().isInterruptionRequested():
            raise InterruptedError("AI 任务已暂停")
        finish_reason = ""
        try:
            content, finish_reason = parse_content(raw)
            result = parse_object(content)
            if not isinstance(result, dict):
                raise ValueError("not a JSON object")
            return result
        except DeepSeekRequestError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError):
            last_issue = "输出被截断" if finish_reason == "length" else "未返回可读取 JSON"
            if attempt == 0:
                continue
    raise DeepSeekRequestError(f"AI 服务本次{last_issue or '未返回可读取 JSON'}，已自动重试一次；请稍后再试。")
