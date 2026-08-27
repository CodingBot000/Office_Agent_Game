from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models import AgentDecision, IntentClassification, SocialImpactClassification
from app.providers.base import DecisionContext, IntentContext, ProviderError, SocialImpactContext
from app.providers.base import ReportContext
from app.models import ReportExtraction
from app.providers.structured import build_report_prompt
from app.providers.structured import (
    build_decision_prompt,
    build_intent_prompt,
    build_social_impact_prompt,
    normalize_decision,
    strict_schema,
)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class OpenAIStructuredExecutor:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key.strip():
            raise ProviderError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.base_url = settings.openai_base_url.rstrip("/")
        self.timeout_seconds = settings.openai_timeout_seconds

    def run(self, model_type: type[StructuredModel], prompt: str) -> StructuredModel:
        schema_name = model_type.__name__.lower()
        payload = {
            "model": self.model,
            "input": prompt,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": strict_schema(model_type.model_json_schema()),
                    "strict": True,
                }
            },
        }
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"OpenAI Responses API failed ({exc.code}): {detail[-500:]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"OpenAI Responses API connection failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("OpenAI Responses API returned an invalid JSON envelope") from exc

        if not isinstance(response_payload, dict):
            raise ProviderError("OpenAI Responses API returned a non-object envelope")

        output_text = response_payload.get("output_text") or self._extract_output_text(response_payload)
        if not output_text:
            raise ProviderError("OpenAI Responses API returned no output_text")
        try:
            return model_type.model_validate(json.loads(output_text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(f"OpenAI Responses API returned invalid {model_type.__name__} JSON") from exc

    def _extract_output_text(self, response_payload: dict[str, object]) -> str | None:
        output = response_payload.get("output")
        if not isinstance(output, list):
            return None
        for item in output:
            if not isinstance(item, dict):
                continue
            contents = item.get("content")
            if not isinstance(contents, list):
                continue
            for content in contents:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        return None


class OpenAIDecisionProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model
        self.executor = OpenAIStructuredExecutor(settings)

    def decide(self, context: DecisionContext) -> AgentDecision:
        decision = self.executor.run(AgentDecision, build_decision_prompt(context))
        if decision.npc_id != context.npc.id:
            raise ProviderError("OpenAI provider returned a decision for the wrong NPC")
        return normalize_decision(decision)


class OpenAIIntentProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model
        self.executor = OpenAIStructuredExecutor(settings)

    def classify(self, context: IntentContext) -> IntentClassification:
        return self.executor.run(IntentClassification, build_intent_prompt(context))


class OpenAISocialImpactProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model
        self.executor = OpenAIStructuredExecutor(settings)

    def classify_social_impact(self, context: SocialImpactContext) -> SocialImpactClassification:
        return self.executor.run(SocialImpactClassification, build_social_impact_prompt(context))


class OpenAIReportProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model
        self.executor = OpenAIStructuredExecutor(settings)

    def extract(self, context: ReportContext) -> ReportExtraction:
        return self.executor.run(ReportExtraction, build_report_prompt(context))
