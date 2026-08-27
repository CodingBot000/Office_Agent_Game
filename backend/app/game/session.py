"""Persisted session aggregate, independent of providers and request execution."""
from dataclasses import dataclass, field
from app.game.seed import CANONICAL_TRUTH, clone_npcs, clone_relationships, clone_world_objects, clone_evidence
from app.models import (NPCState, RelationshipState, WorldObjectState, SocialEventTrace, GameActionTrace,
                        Evidence, EventLogEntry, AgentTrace, FallbackNotice, GameResult, IncidentReportRequest, ReportExtraction)


@dataclass
class GameSession:
    session_id: str
    revision: int | None = None
    turn: int = 0
    current_location: str = "meeting_room"
    incident_status: str = "ACTIVE"
    objective: list[str] = field(
        default_factory=lambda: [
            "장애의 직접 원인과 기여 요인을 파악하세요.",
            "주요 증거를 확보하세요.",
            "팀의 신뢰를 심각하게 훼손하지 마세요.",
            "최종 Incident Report를 제출하세요.",
        ]
    )
    npcs: dict[str, NPCState] = field(default_factory=clone_npcs)
    relationships: dict[str, RelationshipState] = field(default_factory=clone_relationships)
    world_objects: dict[str, WorldObjectState] = field(default_factory=clone_world_objects)
    social_events: list[SocialEventTrace] = field(default_factory=list)
    game_action_traces: list[GameActionTrace] = field(default_factory=list)
    dialogue_refused_npc_ids: set[str] = field(default_factory=set)
    blocked_action_alert: str | None = None
    evidences: dict[str, Evidence] = field(default_factory=clone_evidence)
    events: list[EventLogEntry] = field(default_factory=list)
    agent_traces: list[AgentTrace] = field(default_factory=list)
    fallback_notices: list[FallbackNotice] = field(default_factory=list)
    discovered_evidence: set[str] = field(default_factory=set)
    canonical_truth: list[str] = field(default_factory=lambda: list(CANONICAL_TRUTH))
    completed: bool = False
    result: GameResult | None = None
    report: IncidentReportRequest | None = None
    report_extraction: ReportExtraction | None = None

