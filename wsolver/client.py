"""OpenAI-compatible client for the fixed weak model.

Every completion uses temperature 1.0, top_p 0.95 and reasoning disabled.
The item id header is set once, on the client, so a call site cannot
forget it. The per-item counter refuses to go past the budget cap.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

MODEL_ID = "ibm-granite/granite-4.2-8b"
TEMPERATURE = 1.0
TOP_P = 0.95
ROOT = Path(__file__).resolve().parents[1]


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Settings:
    api_key: str
    base_url: str
    model: str


def load_settings() -> Settings:
    for candidate in (
        os.environ.get("DOTENV_PATH"),
        ROOT / ".env",
        ROOT / "candidate_package" / ".env",
        Path.cwd() / ".env",
    ):
        if candidate and Path(candidate).is_file():
            load_dotenv(candidate, override=False)
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("MODEL") or MODEL_ID
    if not api_key or not base_url:
        raise RuntimeError(
            "Missing API settings. Put OPENROUTER_API_KEY and OPENROUTER_BASE_URL "
            "in .env (not committed) or in the environment."
        )
    return Settings(api_key=api_key, base_url=base_url, model=model)


class ItemClient:
    """One item, one budget. Constructing the client stamps X-Item-Id."""

    def __init__(self, item_id: str, cap: int, settings: Settings | None = None):
        self.item_id = item_id
        self.cap = cap
        self.calls = 0
        settings = settings or load_settings()
        self.model = settings.model
        # Set once for every completion this client makes.
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            default_headers={"X-Item-Id": item_id},
            timeout=120.0,
            max_retries=0,
        )

    def complete(
        self,
        messages: list[dict],
        *,
        json_mode: bool,
        max_tokens: int = 2500,
    ) -> tuple[str, str]:
        """Return (text, finish_reason). Raises BudgetExceeded past the cap.

        A connection that never opened (refused, DNS) is retried without
        consuming the budget, up to two times, because a counting proxy
        never saw a request. A timeout is counted: the proxy may have
        seen it, and it is retried only while budget remains. An HTTP
        429 or 5xx likewise consumes budget and is retried only while
        budget remains. At 1x a timeout or a 5xx is not retried.
        """
        transport_retries = 0
        while True:
            if self.calls >= self.cap:
                raise BudgetExceeded(
                    f"{self.item_id} already used {self.calls}/{self.cap} calls"
                )
            kwargs = dict(
                model=self.model,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_tokens=max_tokens,
                extra_body={"reasoning": {"enabled": False}},
                messages=messages,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            self.calls += 1
            try:
                response = self._client.chat.completions.create(**kwargs)
            except APITimeoutError:
                # The request was sent. A proxy in front of the endpoint
                # may already have counted it, so this call is spent.
                if self.calls >= self.cap:
                    raise
                time.sleep(2.0)
                continue
            except APIConnectionError:
                self.calls -= 1
                transport_retries += 1
                if transport_retries > 2:
                    self.calls += 1
                    raise
                time.sleep(min(2.0 * transport_retries, 8.0))
                continue
            except RateLimitError:
                if self.calls >= self.cap:
                    raise
                time.sleep(4.0)
                continue
            except APIStatusError as exc:
                if exc.status_code >= 500 and self.calls < self.cap:
                    time.sleep(2.0)
                    continue
                raise
            choice = response.choices[0]
            return (choice.message.content or ""), (choice.finish_reason or "stop")


class ReplayClient:
    """Dev stand-in that replays logged completions. No network."""

    def __init__(self, item_id: str, cap: int, responses: list[tuple[str, str]]):
        self.item_id = item_id
        self.cap = cap
        self.calls = 0
        self._responses = list(responses)

    def complete(
        self,
        messages: list[dict],
        *,
        json_mode: bool,
        max_tokens: int = 2500,
    ) -> tuple[str, str]:
        del messages, json_mode, max_tokens
        if self.calls >= self.cap:
            raise BudgetExceeded(self.item_id)
        if self.calls >= len(self._responses):
            raise BudgetExceeded(f"{self.item_id} replay log exhausted")
        text, finish = self._responses[self.calls]
        self.calls += 1
        return text, finish
