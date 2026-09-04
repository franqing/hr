"""OpenAI 兼容 LLM 客户端（httpx）。供画像 PDF 解析（M3）与候选评分（M5）使用。

- 读取 settings 里的 llm_base_url / llm_api_key / llm_model
- chat_json(prompt, system)：要求模型返回严格 JSON；解析失败或不可用抛 LLMUnavailable
- 白名单策略：只发送业务文本，绝不含手机/邮箱（由调用方保证，这里不碰任何个人信息）
"""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("recruit.llm")


class LLMUnavailable(Exception):
    """LLM 未配置或不可用，调用方应降级到规则兜底。"""


def _read_settings() -> dict:
    from . import db
    raw = db.get_settings()

    def plain(key, default=""):
        v = raw.get(key, default)
        return v if isinstance(v, str) else default

    api_key = plain("llm_api_key")
    if api_key:
        from .security import decrypt_str
        try:
            api_key = decrypt_str(api_key)
        except Exception:
            api_key = ""
    return {
        "base_url": plain("llm_base_url", "https://api.deepseek.com/v1").rstrip("/"),
        "api_key": api_key,
        "model": plain("llm_model", "deepseek-chat"),
    }


def chat_json(prompt: str, system: str = "你是一个严谨的数据解析助手。",
              timeout: float = 90) -> dict:
    """调用 LLM 并解析严格 JSON。失败抛 LLMUnavailable（不在这里重试/降级）。"""
    cfg = _read_settings()
    if not cfg["api_key"] or not cfg["base_url"]:
        raise LLMUnavailable("未配置 LLM API Key")
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM 调用失败：%s", e)
        raise LLMUnavailable(f"LLM 调用失败：{e}") from e
