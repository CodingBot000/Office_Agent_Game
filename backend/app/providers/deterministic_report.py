"""Small offline fixture interpreter, never a fallback for a live report provider.

This intentionally supports only the demo vocabulary, not general semantic accuracy.
Scoring is performed separately by the server rubric.
"""
import re

from app.models import ReportClaim, ReportExtraction
from app.providers.base import ReportContext


class DeterministicReportProvider:
    name = "deterministic-mock"
    model = "deterministic-report-fixtures"

    def extract(self, context: ReportContext) -> ReportExtraction:
        patterns = {
            "schema_mismatch": r"api.*(?:schema|스키마|계약)|(?:스키마|계약).*불일치|schema.*(?:change|mismatch|변경)",
            "qa_warning_ignored": r"qa.*(?:경고|검증|warning)|경고.*무시",
            "schedule_pressure": r"일정.*(?:압박|단축|앞당)|schedule pressure|shorter schedule",
            "late_contract_communication": r"공유.*지연|변경.*(?:늦|지연)|communication failure|shared late",
        }
        sources = [("primary_cause", None, context.report.primary_cause)] + [
            ("contributing_factor", index, text) for index, text in enumerate(context.report.contributing_factors)
        ]
        claims = []
        for source, index, text in sources:
            for passage in re.split(r"[.!?\n]", text):
                passage = passage.strip()
                if not passage:
                    continue
                normalized = passage.casefold()
                negated = bool(re.search(r"아니|아닙|아닌|무관|없|\bnot\b|\bnever\b|unrelated", normalized))
                uncertain = bool(re.search(r"아마|불확실|모르|가능성|maybe|uncertain", normalized))
                for criterion in context.criteria:
                    pattern = patterns.get(criterion.id)
                    if pattern and re.search(pattern, normalized):
                        claims.append(ReportClaim(criterion_id=criterion.id,
                            stance="negated" if negated else "uncertain" if uncertain else "affirmed",
                            source=source, source_index=index, quote=passage))
        return ReportExtraction(claims=claims[:30])
