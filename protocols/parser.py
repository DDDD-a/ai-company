"""
AI Company — 消息解析器

将紧凑格式的 Event Stream 文本解析回 Event 对象。
"""

from .verbs import Event, Verb
from typing import Optional
import ast
import re


def parse_compact(raw: str) -> Optional[Event]:
    """
    解析紧凑消息格式为 Event 对象。

    格式: VERB|AGENT|TASK|STATUS|PAYLOAD|@MENTIONS

    解析失败返回 None。
    """
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    # 提取 @mentions
    mentions = extract_mentions(raw)

    # 分离主体与 mentions
    main = raw
    if "@" in main:
        # 找到第一个 @ 的位置
        mention_match = re.search(r"@\S+", main)
        if mention_match:
            main = main[: mention_match.start()].strip()
            # 重新提取 mentions（从完整 raw 中）
            mentions = extract_mentions(raw)

    parts = main.split("|")
    if len(parts) < 3:
        return None

    verb_str = parts[0].strip()
    agent = parts[1].strip() if len(parts) > 1 else ""
    task = parts[2].strip() if len(parts) > 2 else ""
    status = parts[3].strip() if len(parts) > 3 else ""

    payload = {}
    if len(parts) > 4 and parts[4].strip():
        payload = _parse_payload(parts[4].strip())

    try:
        verb = Verb(verb_str)
    except ValueError:
        return None

    return Event(
        verb=verb,
        agent=agent,
        task=task,
        status=status,
        payload=payload,
        mentions=mentions,
    )


def extract_mentions(text: str) -> list[str]:
    """从文本中提取所有 @mentions"""
    mentions = re.findall(r"@([A-Za-z0-9_]+)", text)
    return list(set(mentions))  # 去重


def _parse_payload(payload_str: str) -> dict:
    """解析 payload 字符串为字典"""
    result = {}

    # 尝试多种解析策略
    # 策略 1: 分号分隔的 key:value 对
    if ";" in payload_str:
        for pair in payload_str.split(";"):
            pair = pair.strip()
            if ":" in pair:
                k, v = pair.split(":", 1)
                result[k.strip()] = _cast_value(v.strip())

    # 策略 2: 单 key:value
    elif ":" in payload_str and " " not in payload_str:
        k, v = payload_str.split(":", 1)
        result[k.strip()] = _cast_value(v.strip())

    # 策略 3: JSON 字符串
    elif payload_str.startswith("{"):
        try:
            result = ast.literal_eval(payload_str)
        except (ValueError, SyntaxError):
            try:
                import json

                result = json.loads(payload_str)
            except json.JSONDecodeError:
                result = {"raw": payload_str}

    # 策略 4: 无法解析，作为 raw 保留
    else:
        result = {"text": payload_str}

    return result


def _cast_value(v: str) -> any:
    """尝试将字符串转为合适的 Python 类型"""
    v = v.strip()
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.lower() == "none" or v.lower() == "null":
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v
