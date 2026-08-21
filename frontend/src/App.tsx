import { FormEvent, useEffect, useMemo, useState } from "react";

import { resetSession, startSession, submitAction, submitReport } from "./api";
import type { AgentTrace, GameSnapshot, IntentClassification, NPCState } from "./types";

const quickCommands = [
  { id: "qa-ask", targetLabel: "QA", targetNpcId: "qa_01", dialogue: "배포전에 무슨 문제가 있던거죠?" },
  { id: "qa-accuse", targetLabel: "QA", targetNpcId: "qa_01", dialogue: "이번 장애의 원인을 어떻게 보고 있나요?" },
  { id: "qa-evidence", targetLabel: "QA", targetNpcId: "qa_01", dialogue: "배포 전 경고 메시지를 보여줄 수 있나요?" },
  { id: "backend-evidence", targetLabel: "Backend", targetNpcId: "backend_01", dialogue: "QA 경고 증거를 확인해 주세요." },
  { id: "rollback", targetLabel: "Team", targetNpcId: null, dialogue: "배포를 중단하고 롤백해 주세요." },
];

function App() {
  const [snapshot, setSnapshot] = useState<GameSnapshot | null>(null);
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
  const latestTrace = useMemo<AgentTrace | null>(
    () =>
      snapshot?.agent_traces
        .slice()
        .reverse()
        .find((trace) => trace.npc_id === selectedNpc?.id) ??
      null,
    [selectedNpc?.id, snapshot],
  );

  async function executeCommand(text: string, intentHint?: IntentClassification, targetHintOverride?: string | null) {
    if (!snapshot || !text.trim() || submitting || snapshot.completed) return;
    const submittedText = text.trim();
    setSubmitting(true);
    setError(null);
    setPendingCommand({ text: submittedText, turn: snapshot.turn + 1, status: "pending" });
    try {
      const response = await submitAction(snapshot.session_id, submittedText, intentHint, targetHintOverride);
      setSnapshot(response.snapshot);
      setPendingCommand(null);
      setLastIntent({
        action: response.classified_action,
        provider: response.intent_provider,
        confidence: response.intent_confidence,
        fallback: response.intent_fallback_used,
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

  async function runCommand(event: FormEvent) {
    event.preventDefault();
    await executeCommand(command, undefined, targetHint);
  }

  async function handleReset() {
    if (!snapshot || submitting) return;
    setSubmitting(true);
    setError(null);
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

  if (loading) return <div className="boot-screen">Loading incident workspace…</div>;
  if (!snapshot) {
    return (
      <div className="boot-screen error-screen">
        <p>Backend 연결이 필요합니다.</p>
        <code>{error ?? "Unknown error"}</code>
      </div>
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
          <button className="ghost-button" type="button" onClick={handleReset} disabled={submitting}>
            RESET SESSION
          </button>
        </div>
      </header>

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
                  onClick={() => setSelectedNpcId(npc.id)}
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
              <span>QUICK ACTIONS</span>
              {quickCommands.slice(0, 3).map((quickCommand) => (
                <div className="quick-action-row" key={quickCommand.id}>
                  <span className="quick-target">대상 {quickCommand.targetLabel}</span>
                  <button
                    type="button"
                    onClick={() => {
                      setCommand(quickCommand.dialogue);
                      setTargetHint(quickCommand.targetNpcId);
                    }}
                  >
                    {quickCommand.dialogue}
                  </button>
                </div>
              ))}
            </div>
            {error && !pendingCommand?.error && <p className="inline-error">{error}</p>}
            {lastIntent && (
              <p className="intent-meta">
                INTENT <strong>{lastIntent.action}</strong> · {lastIntent.provider} · {Math.round(lastIntent.confidence * 100)}% confidence
                {lastIntent.fallback ? " · fallback" : ""}
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
                <Metric label="TRUST" value={`${selectedNpc.dynamic_state.trust_toward_player}`} />
                <Metric label="COOPERATION" value={`${selectedNpc.dynamic_state.cooperation}%`} />
              </div>

              <InfoBlock title="KNOWN FACTS">
                <ul className="compact-list">
                  {selectedNpc.known_facts.map((fact) => <li key={fact}>{fact}</li>)}
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
                <AgentInspectorPanel latestTrace={latestTrace} selectedNpc={selectedNpc} />
              )}
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}

function AgentInspectorPanel({ latestTrace, selectedNpc }: { latestTrace: AgentTrace | null; selectedNpc: NPCState }) {
  if (!latestTrace) {
    return (
      <div className="empty-inspector">
        <span className="eyebrow">AGENT INSPECTOR</span>
        <h2>No decision trace yet</h2>
        <p>해당 NPC에게 질문하거나 행동을 요청하면 구조화된 Agent Decision이 여기에 표시됩니다.</p>
      </div>
    );
  }

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
          <div className="trace-line"><span>EMOTION</span><b>{latestTrace.decision.emotion}</b></div>
          <div className="trace-line"><span>STRESS DELTA</span><b>{formatDelta(latestTrace.decision.stress_delta)}</b></div>
          <div className="trace-line"><span>TRUST DELTA</span><b>{formatDelta(latestTrace.decision.trust_delta)}</b></div>
          <div className="trace-line"><span>COOPERATION DELTA</span><b>{formatDelta(latestTrace.decision.cooperation_delta)}</b></div>
          <p className="trace-summary">{latestTrace.decision.dialogue}</p>
          {latestTrace.decision.memory_candidate && (
            <p className="trace-summary">Memory candidate: {latestTrace.decision.memory_candidate.summary}</p>
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
