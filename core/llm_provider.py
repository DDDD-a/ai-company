"""
AI Company — LLM Provider 抽象层

支持 Anthropic SDK 和 OpenAI 兼容接口（DeepSeek / 通义千问 / GLM 等）。
运行时通过环境变量 LLM_PROVIDER 切换，无需修改代码。
"""

import os
from typing import Optional
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """LLM Provider 抽象基类"""

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        """发送消息，返回纯文本响应"""
        ...


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        import anthropic

        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            temperature=temperature,
        )
        return response.content[0].text


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容接口（DeepSeek / 通义千问 / GLM 等）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        from openai import AsyncOpenAI

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("OPENAI_MODEL", "deepseek-chat")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""


def create_provider() -> LLMProvider:
    """工厂函数：根据环境变量创建 LLM Provider 实例"""
    provider_type = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider_type == "anthropic":
        return AnthropicProvider()
    else:
        return OpenAICompatibleProvider()
