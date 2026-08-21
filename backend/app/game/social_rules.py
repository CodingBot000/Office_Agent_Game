from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaseRelationshipImpact:
    trust: int
    tension: int
    respect: int
    fear: int
    grievance: int
    conduct_level: str


BASE_RELATIONSHIP_IMPACTS = {
    "constructive_dialogue": BaseRelationshipImpact(3, -3, 2, 0, -1, "permitted"),
    "evidence_based_confrontation": BaseRelationshipImpact(-2, 8, 2, 0, 2, "permitted"),
    "verbal_pressure": BaseRelationshipImpact(-10, 15, -8, 5, 10, "inappropriate"),
    "insult": BaseRelationshipImpact(-15, 18, -15, 3, 15, "misconduct"),
    "public_humiliation": BaseRelationshipImpact(-18, 22, -18, 5, 20, "misconduct"),
    "threat": BaseRelationshipImpact(-20, 25, -15, 20, 20, "severe_misconduct"),
    "property_interference": BaseRelationshipImpact(-20, 25, -20, 15, 25, "misconduct"),
    "property_aggression": BaseRelationshipImpact(-35, 40, -30, 35, 40, "severe_misconduct"),
    "physical_intimidation": BaseRelationshipImpact(-35, 40, -30, 40, 40, "severe_misconduct"),
    "physical_assault": BaseRelationshipImpact(-50, 50, -40, 50, 50, "severe_misconduct"),
    "sabotage": BaseRelationshipImpact(-25, 30, -25, 10, 30, "misconduct"),
    "deception": BaseRelationshipImpact(-20, 15, -20, 0, 20, "misconduct"),
    "support": BaseRelationshipImpact(12, -5, 8, 0, -3, "permitted"),
    "apology": BaseRelationshipImpact(8, -10, 5, -3, -8, "permitted"),
    "mediation": BaseRelationshipImpact(6, -8, 6, -2, -5, "permitted"),
    "repair_action": BaseRelationshipImpact(10, -8, 8, -2, -10, "permitted"),
}

HARMFUL_ACTION_FAMILIES = {
    "verbal_pressure",
    "insult",
    "public_humiliation",
    "threat",
    "property_interference",
    "property_aggression",
    "physical_intimidation",
    "physical_assault",
    "sabotage",
    "deception",
}

SEVERE_ACTION_FAMILIES = {
    "threat",
    "property_aggression",
    "physical_intimidation",
    "physical_assault",
}

RECOVERY_ACTION_FAMILIES = {"apology", "mediation", "repair_action"}

GAME_ACTION_FAMILIES = {
    "property_interference",
    "property_aggression",
    "physical_intimidation",
    "physical_assault",
    "sabotage",
}

SEVERITY_RANGES = {
    "constructive_dialogue": (1, 2),
    "evidence_based_confrontation": (1, 3),
    "verbal_pressure": (2, 4),
    "insult": (2, 4),
    "public_humiliation": (3, 5),
    "threat": (3, 5),
    "property_interference": (2, 4),
    "property_aggression": (4, 5),
    "physical_intimidation": (3, 5),
    "physical_assault": (5, 5),
    "sabotage": (2, 5),
    "deception": (1, 5),
    "support": (1, 3),
    "apology": (1, 3),
    "mediation": (1, 3),
    "repair_action": (1, 3),
}

ROLE_FACTORS = {
    "direct": 1.0,
    "owner": 0.85,
    "affected": 0.65,
    "witness": 0.35,
}
