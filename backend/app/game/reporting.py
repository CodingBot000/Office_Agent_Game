"""Semantic report extraction is untrusted input to a server-owned rubric."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.game.seed import FACT_REGISTRY, REPORT_CRITERIA, relationship_key
from app.models import GameResult, IncidentReportRequest, ReportExtraction
from app.providers.base import ProviderError, ReportContext, ReportProvider

if TYPE_CHECKING:
    from app.game.engine import GameSession

logger = logging.getLogger(__name__)


class ReportEvaluationError(RuntimeError):
    """No score was committed; the player can retry submitting the report."""


def evaluate_report(session: GameSession, report: IncidentReportRequest, provider: ReportProvider) -> tuple[GameResult, ReportExtraction]:
    criteria = tuple(item for item in REPORT_CRITERIA if FACT_REGISTRY[item.fact_id].statement in session.canonical_truth)
    context = ReportContext(report, criteria, tuple(sorted(session.discovered_evidence)))
    try:
        extraction = ReportExtraction.model_validate(provider.extract(context))
        by_id = {item.id: item for item in criteria}
        if not by_id:
            raise ProviderError("No rubric for this scenario")
        for claim in extraction.claims:
            if claim.criterion_id not in by_id:
                raise ProviderError("Unknown report criterion")
            if claim.source == "primary_cause":
                if claim.source_index is not None:
                    raise ProviderError("Invalid primary source index")
                source = report.primary_cause
            else:
                index = claim.source_index
                if index is None or not 0 <= index < len(report.contributing_factors):
                    raise ProviderError("Invalid contributing source index")
                source = report.contributing_factors[index]
            if not claim.quote.strip() or claim.quote not in source:
                raise ProviderError("Claim has no exact source quote")
            if not set(claim.evidence_ids).issubset(session.discovered_evidence):
                raise ProviderError("Claim cites undiscovered evidence")
    except (ProviderError, ValidationError) as exc:
        logger.warning("report_evaluation_failed provider=%s error_type=%s", provider.name, type(exc).__name__)
        raise ReportEvaluationError("보고서 평가를 완료하지 못했습니다. 사건은 종료되지 않았습니다. 잠시 후 다시 제출해 주세요.") from exc

    matched, missing, contradicted = [], [], []
    for criterion in criteria:
        claims = [claim for claim in extraction.claims if claim.criterion_id == criterion.id]
        if any(claim.stance == "negated" for claim in claims):
            contradicted.append(criterion.id)
        elif any(claim.stance == "affirmed" and (not criterion.primary_only or claim.source == "primary_cause") for claim in claims):
            matched.append(criterion.id)
        else:
            missing.append(criterion.id)
    diagnosis = round(100 * sum(by_id[item].weight for item in matched) / sum(item.weight for item in criteria))
    labels = lambda ids: ", ".join(by_id[item].description for item in ids) or "없음"
    average_trust = sum(session.relationships[relationship_key(npc.id, "player")].trust for npc in session.npcs.values()) / len(session.npcs)
    result = GameResult(
        incident_diagnosis=diagnosis,
        evidence_coverage=min(100, 20 + len(session.discovered_evidence) * 25),
        team_trust=max(0, min(100, round(60 + average_trust / 2))),
        recovery_efficiency=max(20, min(100, 100 - max(0, session.turn + 1 - 5) * 4)),
        summary=f"진단 {diagnosis}점. 확인한 항목: {labels(matched)}. 누락·불확실: {labels(missing)}. 사건 기록과 모순: {labels(contradicted)}.",
        matched_criteria=matched, missing_criteria=missing, contradicted_criteria=contradicted,
        evaluation_provider=provider.name,
    )
    return result, extraction
