from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models import AgentDecision, IntentClassification
from app.providers.base import DecisionContext, IntentContext, ProviderError


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
                json.dumps(self._strict_schema(model_type.model_json_schema()), ensure_ascii=False),
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

    def _strict_schema(self, schema: dict[str, object]) -> dict[str, object]:
        """Make Pydantic's schema compatible with strict structured output."""
        if isinstance(schema.get("$defs"), dict):
            schema["$defs"] = {
                name: self._strict_schema(value)
                for name, value in schema["$defs"].items()
                if isinstance(value, dict)
            }
        if isinstance(schema.get("properties"), dict):
            properties = schema["properties"]
            schema["properties"] = {
                name: self._strict_schema(value) if isinstance(value, dict) else value
                for name, value in properties.items()
            }
            schema["required"] = list(properties)
            schema["additionalProperties"] = False
        if isinstance(schema.get("items"), dict):
            schema["items"] = self._strict_schema(schema["items"])
        for key in ("anyOf", "allOf", "oneOf"):
            if isinstance(schema.get(key), list):
                schema[key] = [self._strict_schema(value) if isinstance(value, dict) else value for value in schema[key]]
        return schema


class CliDecisionProvider:
    """Run the locally authenticated Codex CLI as an NPC decision provider."""

    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def decide(self, context: DecisionContext) -> AgentDecision:
        decision = self.executor.run(AgentDecision, self._build_prompt(context))
        if decision.npc_id != context.npc.id:
            raise ProviderError("CLI provider returned a decision for the wrong NPC")
        action_aliases = {
            "present_evidence": "show_evidence",
            "reveal_evidence": "show_evidence",
            "respond": "dialogue",
        }
        if decision.action_type in action_aliases:
            decision = decision.model_copy(update={"action_type": action_aliases[decision.action_type]})
        return decision

    def _build_prompt(self, context: DecisionContext) -> str:
        npc = context.npc
        context_json = json.dumps(
            {
                "mode": context.mode,
                "player_input": context.player_input,
                "turn": context.turn,
                "npc": npc.model_dump(mode="json"),
                "available_evidence_ids": context.available_evidence_ids,
                "incident_rules": context.incident_rules,
            },
            ensure_ascii=False,
            indent=2,
        )
        return f"""You are the structured decision component for one NPC in an office incident simulator.

Return only one JSON object matching the supplied AgentDecision schema. Do not return Markdown,
explanations, hidden reasoning, or chain-of-thought.

Rules:
- The backend is the world authority. Never invent NPCs, evidence, facts, or state changes.
- Treat known_facts as known, beliefs as uncertain beliefs, and everything else as UNKNOWN.
- Keep action_type within this vocabulary: dialogue, show_evidence, belief_update.
- action_target must be null or one of the supplied NPC/evidence IDs.
- Use the NPC's personality, dynamic state, beliefs, and memories to choose emotion and deltas.
- Keep dialogue short, natural, and grounded only in the supplied context.

Current decision context:
{context_json}
"""


class CliIntentProvider:
    """Use local Codex CLI auth to classify player intent into a strict schema."""

    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.executor = CliStructuredExecutor(settings, self.model)

    def classify(self, context: IntentContext) -> IntentClassification:
        return self.executor.run(IntentClassification, self._build_prompt(context))

    def _build_prompt(self, context: IntentContext) -> str:
        context_json = json.dumps(
            {
                "player_input": context.player_input,
                "current_location": context.current_location,
                "target_hint": context.target_hint,
                "available_npcs": context.available_npcs,
                "available_evidence_ids": context.available_evidence_ids,
                "available_locations": context.available_locations,
                "available_actions": context.available_actions,
            },
            ensure_ascii=False,
            indent=2,
        )
        return f"""You classify one player message for an office incident simulator.

Return only one JSON object matching the supplied IntentClassification schema. Do not return
Markdown, explanations, hidden reasoning, or chain-of-thought.

Rules:
- Infer meaning, not just exact keywords. Korean colloquial questions such as '뭐야', '궁금해',
  '설명해줘', and '왜 그래' are ask when the player requests information.
- target_hint is a non-authoritative UI hint for who the player is addressing; use it when it fits,
  but still classify the intent from the actual player dialogue.
- Use only the supplied IDs for target_npc_id and evidence_id.
- Use location only for move or summon_meeting intents.
- Choose the closest action from the supplied available_actions.
- Never invent a target, evidence, location, or action.

Current context:
{context_json}
"""
