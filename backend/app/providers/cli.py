from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.models import AgentDecision
from app.providers.base import DecisionContext, ProviderError


class CliDecisionProvider:
    """Run the locally authenticated Codex CLI as a structured decision provider."""

    name = "cli"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ai_cli_model
        self.command = settings.ai_cli_command
        self.timeout_seconds = settings.ai_cli_timeout_seconds
        self.working_directory = Path(__file__).resolve().parents[2]

    def decide(self, context: DecisionContext) -> AgentDecision:
        with tempfile.TemporaryDirectory(prefix="office-agent-cli-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "agent_decision.schema.json"
            output_path = temp_path / "agent_decision.json"
            schema = self._strict_schema(AgentDecision.model_json_schema())
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False),
                encoding="utf-8",
            )

            prompt = self._build_prompt(context)
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
                decision = AgentDecision.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ProviderError("CLI provider returned invalid AgentDecision JSON") from exc

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
