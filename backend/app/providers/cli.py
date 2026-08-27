from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
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


class CliStructuredExecutor:
    """Shared Codex CLI runner for structured intent and agent decisions."""

    def __init__(self, settings: Settings, model: str) -> None:
        self.command = settings.ai_cli_command
        self.model = model
        self.timeout_seconds = settings.ai_cli_timeout_seconds
        self.working_directory = Path(__file__).resolve().parents[2]

    def run(self, model_type: type[StructuredModel], prompt: str) -> StructuredModel:
        with tempfile.TemporaryDirectory(prefix="office-agent-cli-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "structured_output.schema.json"
            output_path = temp_path / "structured_output.json"
            schema_path.write_text(
                json.dumps(strict_schema(model_type.model_json_schema()), ensure_ascii=False),
                encoding="utf-8",
            )
            command = [
                self.command,
                "exec",
                "-c",
                "mcp_servers={}",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--model",
                self.model,
                "-",
            ]
            child_env = dict(os.environ)
            child_env.setdefault("TERM", "xterm-256color")
            # CLI auth is the authority for this provider. Do not accidentally
            # make a local API key the credential path for the CLI invocation.
            child_env.pop("OPENAI_API_KEY", None)

            try:
                completed = subprocess.run(
                    command,
                    cwd=self.working_directory,
                    env=child_env,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ProviderError(f"CLI command not found: {self.command}") from exc
            except subprocess.TimeoutExpired as exc:
                raise ProviderError("CLI provider timed out before returning a decision") from exc

            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "CLI provider failed").strip()
                raise ProviderError(f"CLI provider failed: {detail[-500:]}")
            if not output_path.exists():
                raise ProviderError("CLI provider did not write its structured response")

            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                return model_type.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ProviderError(f"CLI provider returned invalid {model_type.__name__} JSON") from exc

class CliDecisionProvider:
    """Run the locally authenticated Codex CLI as an NPC decision provider."""

    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def decide(self, context: DecisionContext) -> AgentDecision:
        decision = self.executor.run(AgentDecision, build_decision_prompt(context))
        if decision.npc_id != context.npc.id:
            raise ProviderError("CLI provider returned a decision for the wrong NPC")
        return normalize_decision(decision)


class CliIntentProvider:
    """Use local Codex CLI auth to classify player intent into a strict schema."""

    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def classify(self, context: IntentContext) -> IntentClassification:
        return self.executor.run(IntentClassification, build_intent_prompt(context))


class CliSocialImpactProvider:
    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def classify_social_impact(self, context: SocialImpactContext) -> SocialImpactClassification:
        return self.executor.run(SocialImpactClassification, build_social_impact_prompt(context))


class CliReportProvider:
    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def extract(self, context: ReportContext) -> ReportExtraction:
        return self.executor.run(ReportExtraction, build_report_prompt(context))
