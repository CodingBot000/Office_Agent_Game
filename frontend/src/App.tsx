import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { resetSession, startSession, submitAction, submitGameAction, submitReport } from "./api";
import { ModeChooser, VisualOffice } from "./VisualOffice";
import type {
  AvailableGameAction,
  AgentTrace,
  GameSnapshot,
  IntentClassification,
  NPCState,
  RelationshipState,
  SocialEventTrace,
} from "./types";

type QuickCommand = {
  id: string;
  dialogue: string;
  requiresEvidenceId?: string;
};

type ViewMode = "chooser" | "dialogue" | "visual";

const recommendedQuickCommands: Record<string, QuickCommand[]> = {
  qa_01: [
    { id: "qa-ask", dialogue: "배포 전에 어떤 문제를 발견했나요?" },
    { id: "qa-evidence", dialogue: "배포 전 경고 메시지를 보여줄 수 있나요?" },
    { id: "qa-follow-up", dialogue: "경고 메시지를 보낸 뒤 어떤 응답을 받았나요?" },
  ],
  backend_01: [
    { id: "backend-ask", dialogue: "API 응답 스키마를 어떻게 변경했고, 왜 배포를 진행했나요?" },
    { id: "backend-verification", dialogue: "배포 전에 QA 경고와 Frontend 반영 상태를 어떻게 확인했나요?" },
    {
      id: "backend-evidence",
      dialogue: "QA가 배포 20분 전에 보낸 경고 증거를 확인해 주세요.",
      requiresEvidenceId: "qa_warning_message",
    },
  ],
  frontend_01: [
    { id: "frontend-ask", dialogue: "API 변경 사항을 언제 전달받았고, 배포 전 검증은 어떻게 진행했나요?" },
    { id: "frontend-failure", dialogue: "로컬 검증이 통과했는데 배포 후 요청이 실패한 이유를 어떻게 보고 있나요?" },
    { id: "frontend-contract", dialogue: "변경된 응답 필드와 실제 프론트엔드 코드의 차이를 확인해 주세요." },
  ],
  pm_01: [
    { id: "pm-ask", dialogue: "릴리스 일정이 하루 앞당겨진 경위와 배포를 서두른 이유를 설명해 주세요." },
    { id: "pm-approval", dialogue: "일정 변경 당시 QA 검증과 배포 승인 절차는 어떻게 합의됐나요?" },
    { id: "pm-warning-follow-up", dialogue: "QA 경고 이후에도 배포를 진행하게 된 승인 절차가 있었나요?" },
  ],
};

function App() {
  const [snapshot, setSnapshot] = useState<GameSnapshot | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("chooser");
  const [lastIntent, setLastIntent] = useState<ActionResponseMeta | null>(null);
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const [targetHint, setTargetHint] = useState<string | null>(null);
  const [selectedNpcId, setSelectedNpcId] = useState("qa_01");
  const [command, setCommand] = useState("");
  const [primaryCause, setPrimaryCause] = useState("");
  const [contributingFactors, setContributingFactors] = useState("");
  const [activeInspectorTab, setActiveInspectorTab] = useState<"npc" | "agent">("npc");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionAlert, setActionAlert] = useState<string | null>(null);
  const visualLocationRequest = useRef<string | null>(null);

  useEffect(() => {
    startSession()
      .then(setSnapshot)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Backend 연결에 실패했습니다."))
      .finally(() => setLoading(false));
  }, []);

  const selectedNpc = useMemo<NPCState | null>(
    () => snapshot?.npcs.find((npc) => npc.id === selectedNpcId) ?? snapshot?.npcs[0] ?? null,
    [selectedNpcId, snapshot],
  );
  const selectedQuickCommands = selectedNpc ? recommendedQuickCommands[selectedNpc.id] ?? [] : [];
  const latestTrace = useMemo<AgentTrace | null>(
    () =>
      snapshot?.agent_traces
        .slice()
        .reverse()
        .find((trace) => trace.npc_id === selectedNpc?.id) ??
      null,
    [selectedNpc?.id, snapshot],
  );
  const selectedRelationship = useMemo<RelationshipState | null>(
    () =>
      snapshot?.relationships.find(
        (relationship) => relationship.source_id === selectedNpc?.id && relationship.target_id === "player",
      ) ?? null,
    [selectedNpc?.id, snapshot],
  );
  const latestSocialTrace = useMemo<SocialEventTrace | null>(
    () =>
      snapshot?.social_events
        .slice()
        .reverse()
        .find((trace) => {
          const selectedId = selectedNpc?.id;
          if (!selectedId) return false;
          return trace.classification.direct_target_ids.includes(selectedId)
            || trace.classification.affected_target_ids.includes(selectedId)
            || trace.requested_classification?.direct_target_ids.includes(selectedId)
            || trace.policy_outcome.relationship_effects.some((effect) => effect.source_id === selectedId);
        }) ?? null,
    [selectedNpc?.id, snapshot],
  );
  const latestFallback = snapshot?.fallback_notices[snapshot.fallback_notices.length - 1] ?? null;

  async function executeCommand(text: string, intentHint?: IntentClassification, targetHintOverride?: string | null) {
    if (!snapshot || !text.trim() || submitting || snapshot.completed) return;
    const submittedText = text.trim();
    setSubmitting(true);
    setError(null);
    setActionAlert(null);
    setPendingCommand({ text: submittedText, turn: snapshot.turn + 1, status: "pending" });
    try {
      const response = await submitAction(snapshot.session_id, submittedText, intentHint, targetHintOverride);
      setSnapshot(response.snapshot);
      setPendingCommand(null);
      setActionAlert(response.alert);
      setLastIntent({
        action: response.classified_action,
        provider: response.intent_provider,
        confidence: response.intent_confidence,
        fallback: response.intent_fallback_used,
        socialProvider: response.social_impact_provider,
        socialFallback: response.social_impact_fallback_used,
      });
      setCommand("");
      setTargetHint(null);
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : "명령 처리에 실패했습니다.";
      setError(message);
      setPendingCommand({ text: submittedText, turn: snapshot.turn + 1, status: "error", error: message });
    } finally {
      setSubmitting(false);
    }
  }

  async function executeGameAction(action: AvailableGameAction) {
    if (!snapshot || submitting || snapshot.completed || !action.enabled) return;
    setSubmitting(true);
    setError(null);
    setActionAlert(null);
    try {
      const response = await submitGameAction(snapshot.session_id, action.id);
      setSnapshot(response.snapshot);
      setActionAlert(response.alert);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "게임 행동 처리에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function runCommand(event: FormEvent) {
    event.preventDefault();
    await executeCommand(command, undefined, targetHint ?? selectedNpc?.id ?? null);
  }

  async function handleReset() {
    if (!snapshot || submitting) return;
    setSubmitting(true);
      setError(null);
      setActionAlert(null);
    try {
      setSnapshot(await resetSession(snapshot.session_id));
      setLastIntent(null);
      setPendingCommand(null);
      setTargetHint(null);
      setSelectedNpcId("qa_01");
      setPrimaryCause("");
      setContributingFactors("");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "세션 초기화에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReport(event: FormEvent) {
    event.preventDefault();
    if (!snapshot || !primaryCause.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const factors = contributingFactors
        .split(",")
        .map((factor) => factor.trim())
        .filter(Boolean);
      setSnapshot(await submitReport(snapshot.session_id, primaryCause.trim(), factors));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "보고서 제출에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  function openDialogue(targetId?: string) {
    if (targetId) {
      setSelectedNpcId(targetId);
      setTargetHint(targetId);
    }
    setCommand("");
    setViewMode("dialogue");
  }

  async function syncVisualLocation(location: string) {
    if (!(["meeting_room", "dev_area", "qa_desk", "pm_desk"] as string[]).includes(location)) {
      return;
    }
    const targetLocation = location as NonNullable<IntentClassification["location"]>;
    if (
      !snapshot
      || snapshot.completed
      || snapshot.current_location === targetLocation
      || submitting
      || visualLocationRequest.current === targetLocation
    ) {
      return;
    }

    visualLocationRequest.current = targetLocation;
    try {
      await executeCommand(`이동: ${targetLocation}`, moveHint(targetLocation), null);
    } finally {
      visualLocationRequest.current = null;
    }
  }

  if (loading) return <div className="boot-screen">Loading incident workspace…</div>;
  if (!snapshot) {
    return (
      <div className="boot-screen error-screen">
        <p>Backend 연결이 필요합니다.</p>
        <code>{error ?? "Unknown error"}</code>
      </div>
    );
  }

  if (viewMode === "chooser") {
    return (
      <ModeChooser
        snapshot={snapshot}
        onChoose={(mode) => setViewMode(mode)}
      />
    );
  }

  if (viewMode === "visual") {
    return (
      <VisualOffice
        key={snapshot.session_id}
        snapshot={snapshot}
        selectedNpcId={selectedNpcId}
        submitting={submitting}
        error={error}
        actionAlert={actionAlert}
        onChooseMode={(mode) => setViewMode(mode)}
        onReset={() => void handleReset()}
        onLocationChange={(location) => void syncVisualLocation(location)}
        onSelectNpc={setSelectedNpcId}
        onTalk={openDialogue}
        onAction={(action) => void executeGameAction(action)}
      />
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">WM</div>
          <div>
            <h1>WHO MESSED UP?</h1>
            <p>AI OFFICE INCIDENT SIMULATOR</p>
          </div>
        </div>
        <div className="topbar-meta">
          <span className={`provider-state ${snapshot.ai_provider}`} title={`Configured AI provider: ${snapshot.ai_provider}`}>
            <i /> {snapshot.ai_provider} / {snapshot.ai_model}
          </span>
          <span className="turn-label">TURN {String(snapshot.turn).padStart(2, "0")} / 20</span>
          <span className={`incident-state ${snapshot.incident_status.toLowerCase()}`}>
            <i /> {snapshot.incident_status}
          </span>
          <div className="view-switch" aria-label="화면 모드 선택">
            <button className="active" type="button" onClick={() => setViewMode("dialogue")}>DIALOGUE</button>
            <button type="button" onClick={() => setViewMode("visual")}>OFFICE VIEW</button>
          </div>
          <button className="ghost-button" type="button" onClick={handleReset} disabled={submitting}>
            RESET SESSION
          </button>
        </div>
      </header>

      {latestFallback && (
        <section className="fallback-banner" role="alert">
          <strong>DETERMINISTIC FALLBACK ACTIVE</strong>
          <span>{latestFallback.stage} · {latestFallback.provider} · turn {String(latestFallback.turn).padStart(2, "0")}</span>
          <p>{latestFallback.reason}</p>
        </section>
      )}

      <section className="workspace-grid">
        <aside className="left-rail">
          <div className="rail-section">
            <div className="section-heading">
              <span>OFFICE</span>
              <span className="muted-label">LIVE</span>
            </div>
            <div className="location-list">
              <button
                className={`location-item ${snapshot.current_location === "meeting_room" ? "active" : ""}`}
                type="button"
                onClick={() => void executeCommand("회의실로 이동한다.", moveHint("meeting_room"))}
                disabled={submitting || snapshot.completed}
              >
                <span className="location-icon">⌂</span>
                <span>Meeting Room</span>
                <b>01</b>
              </button>
              <button
                className={`location-item ${snapshot.current_location === "dev_area" ? "active" : ""}`}
                type="button"
                onClick={() => void executeCommand("개발 구역으로 이동한다.", moveHint("dev_area"))}
                disabled={submitting || snapshot.completed}
              >
                <span className="location-icon">▦</span>
                <span>Dev Area</span>
                <b>02</b>
              </button>
              <button
                className={`location-item ${snapshot.current_location === "qa_desk" ? "active" : ""}`}
                type="button"
                onClick={() => void executeCommand("QA Desk로 이동한다.", moveHint("qa_desk"))}
                disabled={submitting || snapshot.completed}
              >
                <span className="location-icon">⊙</span>
                <span>QA Desk</span>
                <b>01</b>
              </button>
              <button
                className={`location-item ${snapshot.current_location === "pm_desk" ? "active" : ""}`}
                type="button"
                onClick={() => void executeCommand("PM Desk로 이동한다.", moveHint("pm_desk"))}
                disabled={submitting || snapshot.completed}
              >
                <span className="location-icon">▤</span>
                <span>PM Desk</span>
                <b>01</b>
              </button>
            </div>
          </div>

          <div className="rail-section npc-section">
            <div className="section-heading">
              <span>TEAM MEMBERS</span>
              <span className="muted-label">{snapshot.npcs.length} ACTIVE</span>
            </div>
            <div className="npc-list">
              {snapshot.npcs.map((npc) => (
                <button
                  className={`npc-item ${npc.id === selectedNpc?.id ? "selected" : ""}`}
                  key={npc.id}
                  type="button"
                  onClick={() => {
                    setSelectedNpcId(npc.id);
                    setTargetHint(npc.id);
                    setCommand("");
                  }}
                >
                  <span className="avatar">{npc.name.charAt(0)}</span>
                  <span className="npc-copy">
                    <strong>{npc.name}</strong>
                    <small>{npc.role}</small>
                  </span>
                  <span className={`presence ${npc.dynamic_state.emotion}`} />
                </button>
              ))}
            </div>
          </div>

          <div className="objective-block">
            <div className="section-heading">
              <span>OBJECTIVE</span>
              <span className="target-icon">⌁</span>
            </div>
            <ul>
              {snapshot.objective.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </aside>

        <section className="event-column">
          <div className="column-header">
            <div>
              <span className="eyebrow">CURRENT LOCATION</span>
              <h2>{snapshot.current_location.replaceAll("_", " ")}</h2>
            </div>
            <span className="event-count">{snapshot.events.length} EVENTS</span>
          </div>

          <div className="event-log" aria-live="polite">
            {snapshot.events.map((entry) => (
              <article className={`event-entry ${entry.event_type}`} key={entry.id}>
                <div className="event-meta">
                  <span className="event-actor">{entry.actor}</span>
                  <span>TURN {String(entry.turn).padStart(2, "0")}</span>
                </div>
                <p>{entry.message}</p>
              </article>
            ))}
            {pendingCommand && (
              <article className={`event-entry input local-pending ${pendingCommand.status}`}>
                <div className="event-meta">
                  <span className="event-actor">Player</span>
                  <span>TURN {String(pendingCommand.turn).padStart(2, "0")}</span>
                </div>
                <p>{pendingCommand.text}</p>
                {pendingCommand.status === "pending" ? (
                  <div className="response-status" role="status">
                    응답 중<span className="loading-dots" aria-hidden="true">...</span>
                  </div>
                ) : (
                  <div className="response-error" role="alert">ERROR · {pendingCommand.error}</div>
                )}
              </article>
            )}
          </div>

          <div className="command-area">
            <GameActionPanel
              actions={snapshot.available_game_actions}
              submitting={submitting || snapshot.completed}
              onAction={(action) => void executeGameAction(action)}
            />
            <form className="command-form" onSubmit={runCommand}>
              <span className="prompt-symbol">&gt;</span>
              <input
                aria-label="자연어 명령 입력"
                value={command}
                onChange={(event) => {
                  setCommand(event.target.value);
                  setTargetHint(null);
                }}
                placeholder="자연어 명령 입력…"
                disabled={submitting || snapshot.completed}
              />
              <button type="submit" disabled={submitting || !command.trim() || snapshot.completed}>
                {submitting ? "PROCESSING" : "SEND ↗"}
              </button>
            </form>
            <div className="quick-command-row">
              <span>QUICK ACTIONS · {selectedNpc?.name ?? "SELECT MEMBER"}</span>
              {selectedQuickCommands.map((quickCommand) => {
                const requiresEvidence = quickCommand.requiresEvidenceId;
                const evidenceReady = !requiresEvidence
                  || snapshot.evidences.some((evidence) => evidence.id === requiresEvidence && evidence.discovered);
                return (
                  <div className="quick-action-row" key={quickCommand.id}>
                    <span className="quick-target">대상 {selectedNpc?.name ?? "-"}</span>
                    <button
                      type="button"
                      disabled={!evidenceReady || submitting || snapshot.completed}
                      title={!evidenceReady ? "먼저 QA에게 경고 메시지를 요청하세요." : undefined}
                      onClick={() => {
                        setCommand(quickCommand.dialogue);
                        setTargetHint(selectedNpc?.id ?? null);
                      }}
                    >
                      {quickCommand.dialogue}
                    </button>
                  </div>
                );
              })}
            </div>
            {error && !pendingCommand?.error && <p className="inline-error">{error}</p>}
            {actionAlert && <p className="action-alert" role="alert">{actionAlert}</p>}
            {lastIntent && (
              <p className="intent-meta">
                INTENT <strong>{lastIntent.action}</strong> · {lastIntent.provider} · {Math.round(lastIntent.confidence * 100)}% confidence
                {lastIntent.fallback ? " · fallback" : ""}
                {lastIntent.socialProvider ? ` · SOCIAL ${lastIntent.socialProvider}${lastIntent.socialFallback ? " fallback" : ""}` : ""}
              </p>
            )}
          </div>

          {snapshot.completed && snapshot.result && (
            <section className="result-panel">
              <div className="section-heading">
                <span>INCIDENT REPORT</span>
                <span className="resolved-label">RESOLVED</span>
              </div>
              <p>{snapshot.result.summary}</p>
              <div className="score-grid">
                <Score label="Diagnosis" value={snapshot.result.incident_diagnosis} />
                <Score label="Evidence" value={snapshot.result.evidence_coverage} />
                <Score label="Team trust" value={snapshot.result.team_trust} />
                <Score label="Recovery" value={snapshot.result.recovery_efficiency} />
              </div>
            </section>
          )}
        </section>

        <aside className="right-inspector">
          <div className="inspector-tabs">
            <button className={activeInspectorTab === "npc" ? "active" : ""} type="button" onClick={() => setActiveInspectorTab("npc")}>NPC STATE</button>
            <button className={activeInspectorTab === "agent" ? "active" : ""} type="button" onClick={() => setActiveInspectorTab("agent")}>AGENT INSPECTOR</button>
          </div>

          {selectedNpc && (
            <div className="inspector-content">
              {activeInspectorTab === "npc" ? (
                <>
              <div className="inspector-title-row">
                <div>
                  <span className="eyebrow">SELECTED NPC</span>
                  <h2>{selectedNpc.name}</h2>
                  <p>{selectedNpc.role}</p>
                </div>
                <span className={`large-avatar ${selectedNpc.dynamic_state.emotion}`}>{selectedNpc.name.charAt(0)}</span>
              </div>

              <div className="state-grid">
                <Metric label="EMOTION" value={selectedNpc.dynamic_state.emotion} />
                <Metric label="STRESS" value={`${selectedNpc.dynamic_state.stress}%`} />
                <Metric label="TRUST" value={`${selectedRelationship?.trust ?? selectedNpc.dynamic_state.trust_toward_player}`} />
                <Metric label="COOPERATION" value={`${selectedNpc.dynamic_state.cooperation}%`} />
              </div>

              {selectedRelationship && (
                <InfoBlock title="RELATIONSHIP TO PLAYER">
                  <div className="relationship-grid">
                    <Metric label="TRUST" value={`${selectedRelationship.trust}`} />
                    <Metric label="TENSION" value={`${selectedRelationship.tension}`} />
                    <Metric label="RESPECT" value={`${selectedRelationship.respect}`} />
                    <Metric label="FEAR" value={`${selectedRelationship.fear}`} />
                    <Metric label="GRIEVANCE" value={`${selectedRelationship.grievance}`} />
                    <Metric label="REPAIR" value={selectedRelationship.repair_stage} />
                  </div>
                  {(selectedRelationship.trust_ceiling !== null || selectedRelationship.fear_floor > 0) && (
                    <p className="relationship-restriction">
                      Active restriction · trust ceiling {selectedRelationship.trust_ceiling ?? "none"} · fear floor {selectedRelationship.fear_floor}
                    </p>
                  )}
                  {snapshot.dialogue_refused_npc_ids.includes(selectedNpc.id) && (
                    <p className="dialogue-refused">NORMAL DIALOGUE REFUSED · mediation required</p>
                  )}
                </InfoBlock>
              )}

              <InfoBlock title="PLAYER HAND">
                <div className="world-object-list">
                  {snapshot.player_inventory.held_object_ids.length > 0 ? (
                    snapshot.player_inventory.held_object_ids.map((objectId) => {
                      const item = snapshot.world_objects.find((worldObject) => worldObject.id === objectId);
                      return item ? (
                        <div className="world-object-row" key={item.id}>
                          <span className="object-condition held">HELD</span>
                          <div><strong>{item.name}</strong><small>{item.id} · owner {item.owner_id ?? "shared"}</small></div>
                        </div>
                      ) : null;
                    })
                  ) : (
                    <p className="trace-summary">Hands are empty.</p>
                  )}
                </div>
              </InfoBlock>

              <InfoBlock title="OWNED WORLD OBJECTS">
                <div className="world-object-list">
                  {snapshot.world_objects.filter((item) => item.owner_id === selectedNpc.id).map((item) => (
                    <div className="world-object-row" key={item.id}>
                      <span className={`object-condition ${item.holder_id === "player" ? "held" : item.condition}`}>
                        {item.holder_id === "player" ? "HELD BY PLAYER" : item.condition}
                      </span>
                      <div><strong>{item.name}</strong><small>{item.id} · {item.location}</small></div>
                    </div>
                  ))}
                </div>
              </InfoBlock>

              <InfoBlock title="KNOWN FACTS">
                <ul className="compact-list">
                  {selectedNpc.known_facts.map((fact, index) => (
                    <li key={selectedNpc.known_fact_ids[index] ?? fact}>
                      <span>{fact}</span>
                      {selectedNpc.known_fact_ids[index] && <small>{selectedNpc.known_fact_ids[index]}</small>}
                    </li>
                  ))}
                </ul>
              </InfoBlock>

              <InfoBlock title="BELIEFS">
                <ul className="compact-list">
                  {selectedNpc.beliefs.map((belief) => (
                    <li key={`${belief.subject}-${belief.belief}`}>
                      <span>{belief.belief}</span>
                      <small>{Math.round(belief.confidence * 100)}% confidence</small>
                    </li>
                  ))}
                </ul>
              </InfoBlock>

              <InfoBlock title="EVIDENCE">
                <div className="evidence-list">
                  {snapshot.evidences.map((evidence) => (
                    <div className={`evidence-row ${evidence.discovered ? "discovered" : "locked"}`} key={evidence.id}>
                      <span className="evidence-status">{evidence.discovered ? "✓" : "—"}</span>
                      <div>
                        <strong>{evidence.title}</strong>
                        <p>{evidence.discovered ? evidence.content : evidence.summary}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </InfoBlock>

              <form className="report-form" onSubmit={handleReport}>
                <div className="section-heading"><span>FINAL REPORT</span><span className="muted-label">{snapshot.completed ? "LOCKED" : "OPTIONAL"}</span></div>
                <textarea
                  value={primaryCause}
                  onChange={(event) => setPrimaryCause(event.target.value)}
                  placeholder="Primary cause…"
                  disabled={submitting || snapshot.completed}
                />
                <input
                  value={contributingFactors}
                  onChange={(event) => setContributingFactors(event.target.value)}
                  placeholder="Contributing factors, comma separated"
                  disabled={submitting || snapshot.completed}
                />
                <button type="submit" disabled={submitting || !primaryCause.trim() || snapshot.completed}>SUBMIT INCIDENT REPORT</button>
              </form>
                </>
              ) : (
                <AgentInspectorPanel
                  latestTrace={latestTrace}
                  latestSocialTrace={latestSocialTrace}
                  selectedNpc={selectedNpc}
                  selectedRelationship={selectedRelationship}
                  snapshot={snapshot}
                />
              )}
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}

function AgentInspectorPanel({
  latestTrace,
  latestSocialTrace,
  selectedNpc,
  selectedRelationship,
  snapshot,
}: {
  latestTrace: AgentTrace | null;
  latestSocialTrace: SocialEventTrace | null;
  selectedNpc: NPCState;
  selectedRelationship: RelationshipState | null;
  snapshot: GameSnapshot;
}) {
  if (latestSocialTrace && (!latestTrace || latestSocialTrace.turn >= latestTrace.turn)) {
    return (
      <SocialInspectorPanel
        trace={latestSocialTrace}
        selectedNpc={selectedNpc}
        selectedRelationship={selectedRelationship}
        snapshot={snapshot}
      />
    );
  }

  if (!latestTrace) {
    return (
      <div className="empty-inspector">
        <span className="eyebrow">AGENT INSPECTOR</span>
        <h2>No decision trace yet</h2>
        <p>해당 NPC에게 질문하거나 행동을 요청하면 구조화된 Agent Decision이 여기에 표시됩니다.</p>
      </div>
    );
  }

  const groundingDecision = latestTrace.requested_decision ?? latestTrace.decision;
  const factStatementById = new Map(
    latestTrace.known_fact_ids.map((factId, index) => [factId, latestTrace.known_facts[index] ?? factId]),
  );

  return (
    <>
      <div className="inspector-title-row">
        <div>
          <span className="eyebrow">AGENT INSPECTOR</span>
          <h2>Decision Trace</h2>
          <p>{selectedNpc.name} · turn {String(latestTrace.turn).padStart(2, "0")}</p>
        </div>
        <span className="trace-badge">{latestTrace.provider}</span>
      </div>

      <InfoBlock title="CONTEXT SUMMARY">
        <p className="trace-summary">{latestTrace.context_summary}</p>
      </InfoBlock>

      <InfoBlock title="KNOWN FACTS">
        <ul className="compact-list">
          {latestTrace.known_facts.map((fact) => <li key={fact}>{fact}</li>)}
        </ul>
      </InfoBlock>

      <InfoBlock title="KNOWLEDGE REFERENCES">
        {groundingDecision.knowledge_refs.length > 0 ? (
          <div className="knowledge-ref-list">
            {groundingDecision.knowledge_refs.map((factId) => {
              const known = latestTrace.known_fact_ids.includes(factId);
              return (
                <div className="knowledge-ref-row" key={factId}>
                  <span className={known ? "pass" : "fail"}>{known ? "✓" : "×"}</span>
                  <div>
                    <strong>{factId}</strong>
                    <p>{factStatementById.get(factId) ?? "Unknown or unauthorized Fact ID"}</p>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="trace-summary">No factual knowledge reference was used.</p>
        )}
      </InfoBlock>

      <InfoBlock title="RETRIEVED RULES">
        {latestTrace.retrieved_rules.length > 0 ? (
          <ul className="compact-list">
            {latestTrace.retrieved_rules.map((rule) => <li key={rule}>{rule}</li>)}
          </ul>
        ) : (
          <p className="trace-summary">No external rule retrieved for this decision.</p>
        )}
      </InfoBlock>

      <InfoBlock title="STRUCTURED DECISION">
        <div className="trace-block">
          <div className="trace-line"><span>ACTION</span><b>{latestTrace.decision.action_type}</b></div>
          <div className="trace-line"><span>GROUNDING</span><b>{latestTrace.decision.grounding_type}</b></div>
          <div className="trace-line"><span>EMOTION</span><b>{latestTrace.decision.emotion}</b></div>
          <div className="trace-line"><span>STRESS DELTA</span><b>{formatDelta(latestTrace.decision.stress_delta)}</b></div>
          <div className="trace-line"><span>TRUST DELTA</span><b>{formatDelta(latestTrace.decision.trust_delta)}</b></div>
          <div className="trace-line"><span>COOPERATION DELTA</span><b>{formatDelta(latestTrace.decision.cooperation_delta)}</b></div>
          <p className="trace-summary">{latestTrace.decision.dialogue}</p>
          {latestTrace.decision.memory_candidate && (
            <p className="trace-summary">Memory candidate: {latestTrace.decision.memory_candidate.summary}</p>
          )}
          {latestTrace.decision.relationship_updates.length > 0 && (
            <p className="trace-summary">
              Relationship updates: {latestTrace.decision.relationship_updates.map((update) => `${update.target_npc_id} trust ${formatDelta(update.trust_delta)}, tension ${formatDelta(update.tension_delta)}`).join(" · ")}
            </p>
          )}
        </div>
      </InfoBlock>

      <InfoBlock title="GUARDRAIL RESULT">
        <div className="guardrail-list">
          {latestTrace.guardrails.map((check) => (
            <div className="guardrail-row" key={check.name}>
              <span className={check.passed ? "pass" : "fail"}>{check.passed ? "✓" : "×"}</span>
              <span>{check.name.replaceAll("_", " ")}</span>
            </div>
          ))}
        </div>
        {latestTrace.fallback_used && <p className="fallback-note">Fallback applied after an invalid provider result.</p>}
      </InfoBlock>
    </>
  );
}

function GameActionPanel({
  actions,
  submitting,
  onAction,
}: {
  actions: AvailableGameAction[];
  submitting: boolean;
  onAction: (action: AvailableGameAction) => void;
}) {
  return (
    <section className="game-action-panel" aria-label="Game actions">
      <div className="section-heading">
        <span>GAME ACTIONS</span>
        <span className="muted-label">BUTTON ONLY</span>
      </div>
      {actions.length > 0 ? (
        <div className="game-action-list">
          {actions.map((action) => (
            <button
              className="game-action-button"
              key={action.id}
              type="button"
              disabled={submitting || !action.enabled}
              title={action.disabled_reason ?? action.id}
              onClick={() => onAction(action)}
            >
              <span>{action.label}</span>
              <small>{action.family.replaceAll("_", " ")}</small>
            </button>
          ))}
        </div>
      ) : (
        <p className="game-action-empty">No game actions available in this location.</p>
      )}
    </section>
  );
}

function SocialInspectorPanel({
  trace,
  selectedNpc,
  selectedRelationship,
  snapshot,
}: {
  trace: SocialEventTrace;
  selectedNpc: NPCState;
  selectedRelationship: RelationshipState | null;
  snapshot: GameSnapshot;
}) {
  const classification = trace.classification;
  const npcNameById = new Map(snapshot.npcs.map((npc) => [npc.id, npc.name]));
  const objectById = new Map(snapshot.world_objects.map((item) => [item.id, item]));
  const object = classification.object_id ? objectById.get(classification.object_id) : null;

  return (
    <>
      <div className="inspector-title-row">
        <div>
          <span className="eyebrow">AGENT INSPECTOR</span>
          <h2>Social Policy Trace</h2>
          <p>{selectedNpc.name} · turn {String(trace.turn).padStart(2, "0")}</p>
        </div>
        <span className="trace-badge policy">{trace.provider}</span>
      </div>

      <InfoBlock title="SOCIAL IMPACT">
        <div className="trace-block">
          <div className="trace-line"><span>ACTION FAMILY</span><b>{classification.action_family}</b></div>
          <div className="trace-line"><span>SEVERITY</span><b>{classification.severity} / 5</b></div>
          <div className="trace-line"><span>INTENTIONALITY</span><b>{classification.intentionality}</b></div>
          <div className="trace-line"><span>CONDUCT</span><b className={`conduct-${trace.policy_outcome.conduct_level}`}>{trace.policy_outcome.conduct_level}</b></div>
          <p className="trace-summary">{trace.player_input}</p>
          <p className="reason-codes">{classification.reason_codes.join(" · ") || "no reason code"}</p>
        </div>
      </InfoBlock>

      <InfoBlock title="TARGETS & OBJECT">
        <div className="trace-block">
          <div className="trace-line">
            <span>DIRECT</span>
            <b>{classification.direct_target_ids.map((id) => npcNameById.get(id) ?? id).join(", ") || "none"}</b>
          </div>
          <div className="trace-line">
            <span>AFFECTED</span>
            <b>{classification.affected_target_ids.map((id) => npcNameById.get(id) ?? id).join(", ") || "none"}</b>
          </div>
          <div className="trace-line"><span>OBJECT</span><b>{object?.name ?? classification.object_id ?? "none"}</b></div>
          {object && <p className="trace-summary">owner {npcNameById.get(object.owner_id ?? "") ?? object.owner_id ?? "shared"} · {object.condition} · {object.location}</p>}
        </div>
      </InfoBlock>

      <InfoBlock title="RELATIONSHIP POLICY">
        <div className="relationship-effect-list">
          {trace.policy_outcome.relationship_effects.map((effect) => (
            <div className="relationship-effect-row" key={`${effect.source_id}-${effect.target_id}`}>
              <strong>{npcNameById.get(effect.source_id) ?? effect.source_id} → Player</strong>
              <span>trust {formatDelta(effect.trust_delta)} · tension {formatDelta(effect.tension_delta)}</span>
              <span>respect {formatDelta(effect.respect_delta)} · fear {formatDelta(effect.fear_delta)} · grievance {formatDelta(effect.grievance_delta)}</span>
              <small>{effect.reason_codes.join(" · ")}</small>
            </div>
          ))}
        </div>
        {selectedRelationship && (
          <p className="policy-final-state">
            Current {selectedNpc.name}: trust {selectedRelationship.trust} · tension {selectedRelationship.tension} · respect {selectedRelationship.respect} · fear {selectedRelationship.fear} · grievance {selectedRelationship.grievance}
          </p>
        )}
      </InfoBlock>

      <InfoBlock title="MODIFIERS">
        {trace.policy_outcome.applied_modifiers.length > 0 ? (
          <div className="modifier-list">
            {trace.policy_outcome.applied_modifiers.map((modifier) => (
              <span key={modifier.code}>{modifier.code} ×{modifier.multiplier.toFixed(1)}</span>
            ))}
          </div>
        ) : (
          <p className="trace-summary">No harmful-action multiplier applied.</p>
        )}
      </InfoBlock>

      <InfoBlock title="CONSEQUENCES">
        {trace.policy_outcome.mandatory_world_events.length > 0 ? (
          <ul className="compact-list">
            {trace.policy_outcome.mandatory_world_events.map((event, index) => (
              <li key={`${event.event_type}-${index}`}>
                <span>{event.event_type.replaceAll("_", " ")}</span>
                <small>{event.detail}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="trace-summary">No mandatory world event.</p>
        )}
      </InfoBlock>

      <InfoBlock title="GUARDRAIL RESULT">
        <div className="guardrail-list">
          {trace.guardrails.map((check) => (
            <div className="guardrail-row" key={check.name} title={check.detail}>
              <span className={check.passed ? "pass" : "fail"}>{check.passed ? "✓" : "×"}</span>
              <span>{check.name.replaceAll("_", " ")}</span>
            </div>
          ))}
        </div>
        {!trace.fallback_used && (
          <p className="policy-engine-note">POLICY ENGINE · deterministic server policy applied normally</p>
        )}
        {trace.fallback_used && (
          <p className="fallback-note">
            Deterministic fallback used after provider/guardrail failure.
            {trace.requested_classification ? ` Rejected: ${trace.requested_classification.action_family}.` : ""}
          </p>
        )}
      </InfoBlock>
    </>
  );
}

function formatDelta(value: number) {
  return value > 0 ? `+${value}` : String(value);
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function InfoBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="info-block"><div className="info-title">{title}</div>{children}</section>;
}

function Score({ label, value }: { label: string; value: number }) {
  return <div className="score"><span>{label}</span><strong>{value}<small>%</small></strong></div>;
}

interface ActionResponseMeta {
  action: string;
  provider: "cli" | "openai" | "deterministic-mock" | "ui";
  confidence: number;
  fallback: boolean;
  socialProvider: "cli" | "openai" | "deterministic-mock" | null;
  socialFallback: boolean;
}

function moveHint(location: NonNullable<IntentClassification["location"]>): IntentClassification {
  return { intent: "move", location, confidence: 1 };
}

interface PendingCommand {
  text: string;
  turn: number;
  status: "pending" | "error";
  error?: string;
}

export default App;
