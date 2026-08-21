from copy import deepcopy

from app.models import Belief, DynamicState, Evidence, NPCState, Personality, Relationship


CANONICAL_TRUTH = [
    "PM moved the release date one day earlier.",
    "The team lead required deployment today.",
    "Backend Developer changed the API response schema.",
    "Frontend Developer received the change late.",
    "QA found a critical issue 20 minutes before deployment.",
    "QA sent a warning message, but the team lead did not confirm it.",
    "Backend Developer knew the risk and deployed anyway.",
    "Some frontend requests failed after deployment, causing the outage.",
]

INCIDENT_RULES = [
    "A critical QA issue must block production deployment until it is resolved.",
    "Production deployment requires confirmation from the responsible developer and team lead.",
]


def build_initial_npcs() -> dict[str, NPCState]:
    return {
        "backend_01": NPCState(
            id="backend_01",
            name="Backend Developer",
            role="Backend Developer",
            personality=Personality(assertiveness=72, cooperativeness=48, risk_aversion=35, blame_sensitivity=75),
            dynamic_state=DynamicState(emotion="tense", stress=45, trust_toward_player=10, cooperation=55),
            known_facts=[
                "I deployed the release.",
                "I changed the API response schema.",
                "The schedule was shortened.",
                "QA mentioned a risk before deployment.",
            ],
            beliefs=[Belief(subject="incident", belief="Schedule pressure is the root cause.", confidence=0.7)],
            relationships=[
                Relationship(target_npc_id="qa_01", trust=10, tension=45),
                Relationship(target_npc_id="frontend_01", trust=25, tension=30),
            ],
        ),
        "frontend_01": NPCState(
            id="frontend_01",
            name="Frontend Developer",
            role="Frontend Developer",
            personality=Personality(assertiveness=45, cooperativeness=70, risk_aversion=65, blame_sensitivity=45),
            dynamic_state=DynamicState(emotion="worried", stress=35, trust_toward_player=20, cooperation=70),
            known_facts=[
                "The API change was shared late.",
                "My code passed the last local check.",
            ],
            beliefs=[Belief(subject="incident", belief="The change-sharing process failed.", confidence=0.8)],
            relationships=[Relationship(target_npc_id="backend_01", trust=15, tension=40)],
        ),
        "qa_01": NPCState(
            id="qa_01",
            name="QA Engineer",
            role="QA Engineer",
            personality=Personality(assertiveness=65, cooperativeness=60, risk_aversion=90, blame_sensitivity=80),
            dynamic_state=DynamicState(emotion="guarded", stress=55, trust_toward_player=15, cooperation=65),
            known_facts=[
                "I found a critical issue before deployment.",
                "I sent a warning message.",
                "I do not have deployment permission.",
            ],
            beliefs=[Belief(subject="incident", belief="The warning was ignored before deployment.", confidence=0.85)],
            relationships=[Relationship(target_npc_id="backend_01", trust=0, tension=60)],
        ),
        "pm_01": NPCState(
            id="pm_01",
            name="PM / Planner",
            role="PM / Planner",
            personality=Personality(assertiveness=68, cooperativeness=55, risk_aversion=40, blame_sensitivity=60),
            dynamic_state=DynamicState(emotion="urgent", stress=50, trust_toward_player=5, cooperation=60),
            known_facts=[
                "The release date moved one day earlier.",
                "Business stakeholders asked for a shorter schedule.",
            ],
            beliefs=[Belief(subject="incident", belief="The technical team did not protect deployment quality.", confidence=0.65)],
        ),
    }


def build_initial_evidence() -> dict[str, Evidence]:
    return {
        "qa_warning_message": Evidence(
            id="qa_warning_message",
            title="QA warning message",
            summary="Critical issue report sent 20 minutes before deployment.",
            content=(
                "[16:40] QA: Critical — API response mismatch found in production-like test. "
                "Recommend blocking deployment until the contract is verified."
            ),
            source_npc_id="qa_01",
        ),
        "api_schema_diff": Evidence(
            id="api_schema_diff",
            title="API schema diff",
            summary="The response field contract changed in the release branch.",
            content=(
                "response.data.items changed to response.payload.items. "
                "Frontend consumer update was not included in the same release."
            ),
            source_npc_id="backend_01",
        ),
        "release_timeline": Evidence(
            id="release_timeline",
            title="Release timeline",
            summary="Schedule change and deployment milestones.",
            content=(
                "Release moved one day earlier. QA warning at 16:40. "
                "Production deployment began at 17:00."
            ),
            source_npc_id="pm_01",
        ),
    }


def clone_npcs() -> dict[str, NPCState]:
    return deepcopy(build_initial_npcs())


def clone_evidence() -> dict[str, Evidence]:
    return deepcopy(build_initial_evidence())
