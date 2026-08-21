import { FormEvent, useEffect, useMemo, useState } from "react";

import { resetSession, startSession, submitAction, submitReport } from "./api";
import type { AgentTrace, GameSnapshot, NPCState } from "./types";

const quickCommands = [
  "QA에게 배포 전 문제를 질문한다.",
  "QA가 장애의 책임자라고 비난한다.",
  "QA 경고 메시지 기록을 확인한다.",
  "백엔드에게 QA 증거를 제시한다.",
  "배포 중단 및 롤백을 지시한다.",
];

function App() {
  const [snapshot, setSnapshot] = useState<GameSnapshot | null>(null);
  const [selectedNpcId, setSelectedNpcId] = useState("qa_01");
  const [command, setCommand] = useState("");
  const [primaryCause, setPrimaryCause] = useState("");
  const [contributingFactors, setContributingFactors] = useState("");
  const [showInspector, setShowInspector] = useState(true);
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
      snapshot?.agent_traces[snapshot.agent_traces.length - 1] ??
      null,
    [selectedNpc?.id, snapshot],
  );

  async function runCommand(event: FormEvent) {
    event.preventDefault();
    if (!snapshot || !command.trim() || submitting || snapshot.completed) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await submitAction(snapshot.session_id, command.trim());
      setSnapshot(response.snapshot);
      setCommand("");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "명령 처리에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReset() {
    if (!snapshot || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      setSnapshot(await resetSession(snapshot.session_id));
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
              <button className="location-item active" type="button">
                <span className="location-icon">⌂</span>
                <span>Meeting Room</span>
                <b>01</b>
              </button>
              <button className="location-item" type="button" onClick={() => setCommand("개발 구역으로 이동한다.")}>
                <span className="location-icon">▦</span>
                <span>Dev Area</span>
                <b>02</b>
              </button>
              <button className="location-item" type="button" onClick={() => setCommand("QA Desk로 이동한다.")}>
                <span className="location-icon">⊙</span>
                <span>QA Desk</span>
                <b>01</b>
              </button>
              <button className="location-item" type="button" onClick={() => setCommand("PM Desk로 이동한다.")}>
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
          </div>

          <div className="command-area">
            <form className="command-form" onSubmit={runCommand}>
              <span className="prompt-symbol">&gt;</span>
              <input
                aria-label="자연어 명령 입력"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
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
                <button key={quickCommand} type="button" onClick={() => setCommand(quickCommand)}>
                  {quickCommand.replace(/[.。]/g, "").slice(0, 18)}
                </button>
              ))}
            </div>
            {error && <p className="inline-error">{error}</p>}
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
            <button className="active" type="button">NPC STATE</button>
            <button type="button" onClick={() => setShowInspector((current) => !current)}>AGENT INSPECTOR</button>
          </div>

          {selectedNpc && (
            <div className="inspector-content">
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

              {showInspector && latestTrace && (
                <InfoBlock title="LATEST AGENT TRACE">
                  <div className="trace-block">
                    <div className="trace-line"><span>PROVIDER</span><b>{latestTrace.provider}</b></div>
                    <p className="trace-summary">{latestTrace.context_summary}</p>
                    <div className="trace-line"><span>ACTION</span><b>{latestTrace.decision.action_type}</b></div>
                    <div className="guardrail-list">
                      {latestTrace.guardrails.map((check) => (
                        <div className="guardrail-row" key={check.name}>
                          <span className={check.passed ? "pass" : "fail"}>{check.passed ? "✓" : "×"}</span>
                          <span>{check.name.replaceAll("_", " ")}</span>
                        </div>
                      ))}
                    </div>
                    {latestTrace.fallback_used && <p className="fallback-note">Fallback applied after invalid decision.</p>}
                  </div>
                </InfoBlock>
              )}

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
            </div>
          )}
        </aside>
      </section>
    </main>
  );
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

export default App;
