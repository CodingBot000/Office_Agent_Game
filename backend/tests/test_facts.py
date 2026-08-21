from app.game.seed import FACT_DEFINITIONS, FACT_REGISTRY, build_initial_evidence, build_initial_npcs


def test_fact_registry_has_unique_ids_and_valid_evidence_sources() -> None:
    evidence_ids = set(build_initial_evidence())

    assert len(FACT_REGISTRY) == len(FACT_DEFINITIONS)
    assert all(fact.id and fact.statement for fact in FACT_DEFINITIONS)
    assert all(evidence_id in evidence_ids for fact in FACT_DEFINITIONS for evidence_id in fact.source_evidence_ids)


def test_every_npc_known_fact_id_exists_in_registry() -> None:
    for npc in build_initial_npcs().values():
        assert npc.known_fact_ids
        assert all(fact_id in FACT_REGISTRY for fact_id in npc.known_fact_ids)
        assert npc.known_facts == [FACT_REGISTRY[fact_id].statement for fact_id in npc.known_fact_ids]
