"""
AI Company — 通信协议层：动词枚举与事件消息结构

协议设计原则：
- 追求极致效率与准确性，忽略人类可读性
- 消息只传递事件指针，内容存于 Shared State
- 每条消息约 15-30 token
"""

from enum import Enum
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import time
import uuid


class Verb(str, Enum):
    """通信动词 — 最小完备集（10个）"""

    # HR → Worker：分配任务
    ASN = "ASN"
    # Worker → 系统：确认接收
    ACK = "ACK"
    # Worker → 系统：进度更新
    UPD = "UPD"
    # Worker → 系统：阻塞等待
    BLK = "BLK"
    # Worker → HR/Director：请求决策
    REQ = "REQ"
    # HR/Director → Worker：响应 REQ 请求
    RES = "RES"
    # Worker → 系统：冲突上报
    CON = "CON"
    # Worker → 系统：自报完成（待验收）
    DON = "DON"
    # QA → 系统：验收结果
    VAL = "VAL"
    # Director → 任意：干预指令
    DIR = "DIR"
    # Observer → Director：异常预警
    ALERT = "ALERT"


class Event(BaseModel):
    """
    工作池事件数据结构

    Append-Only，不可修改，全局可见。
    Worker 默认不主动监听，只在 mentions 包含自身 ID 时激活。
    """

    id: str = ""
    timestamp: int = 0
    verb: Verb
    agent: str
    task: str
    status: str = ""
    payload: Dict[str, Any] = {}
    mentions: List[str] = []

    def model_post_init(self, __context):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = int(time.time() * 1000)

    def to_compact(self) -> str:
        """
        序列化为紧凑格式（供 Event Stream 和日志使用）

        格式: VERB|AGENT|TASK|STATUS|PAYLOAD|@MENTIONS

        示例:
          ASN|HR|#auth_api|P1|agent:BE @BE
          UPD|BE|#auth_api|50%|impl:jwt;next:token_refresh
          DON|QA|#auth_api|PASS
        """
        payload_str = ""
        if self.payload:
            parts = []
            for k, v in self.payload.items():
                if isinstance(v, str) and " " not in v and ";" not in v and ":" not in v:
                    parts.append(f"{k}:{v}")
                else:
                    parts.append(f"{k}:{str(v)[:50]}")
            payload_str = ";".join(parts)

        mention_str = ""
        if self.mentions:
            mention_str = " " + " ".join(f"@{m}" for m in self.mentions)

        return f"{self.verb.value}|{self.agent}|{self.task}|{self.status}|{payload_str}{mention_str}".rstrip("|")

    def mentions_agent(self, agent_id: str) -> bool:
        """检查事件是否 @ 了指定 Agent"""
        return agent_id in self.mentions
