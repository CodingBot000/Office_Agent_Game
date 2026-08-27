from app.game.seed import FACT_DEFINITIONS, FACT_REGISTRY, build_initial_evidence, build_initial_npcs


def test_disclosed_document_facts_are_valid_and_do_not_include_private_inferences():
    from app.game.seed import EVIDENCE_DISCLOSED_FACT_IDS

    for evidence_id, fact_ids in EVIDENCE_DISCLOSED_FACT_IDS.items():
        assert evidence_id in build_initial_evidence()
        assert all(evidence_id in FACT_REGISTRY[fid].source_evidence_ids and FACT_REGISTRY[fid].revealable for fid in fact_ids)
    assert "team_lead_did_not_confirm_warning" not in EVIDENCE_DISCLOSED_FACT_IDS["qa_warning_message"]


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


def test_every_npc_knows_the_shared_responsibility_map() -> None:
    responsibility_fact_ids = {
        "backend_owns_release_execution",
        "backend_owns_api_schema_change",
        "pm_owns_schedule_pressure",
        "qa_owns_verification",
    }

    for npc in build_initial_npcs().values():
        assert responsibility_fact_ids.issubset(set(npc.known_fact_ids))
