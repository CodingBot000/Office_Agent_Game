import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { formatEmotion, formatWorldObjectName, GameActionLabel } from "./display";
import type { AvailableGameAction, GameSnapshot, NPCState } from "./types";
import type { GameDialogueController } from "./dialogue/useGameDialogue";
import { GameDialoguePanel } from "./dialogue/GameDialoguePanel";
import { useGameKeyboard } from "./input/useGameKeyboard";

export type VisualMode = "dialogue" | "visual";

type WorldPoint = {
  x: number;
  y: number;
};

type SpriteDirection = "front" | "back" | "left" | "right";

type VisualOfficeProps = {
  snapshot: GameSnapshot;
  selectedNpcId: string;
  submitting: boolean;
  error: string | null;
  actionAlert: string | null;
  onChooseMode: (mode: VisualMode) => void;
  onReset: () => void;
  onLocationChange: (location: string) => void;
  onSelectNpc: (id: string) => void;
  dialogue: GameDialogueController;
  onAction: (action: AvailableGameAction) => Promise<boolean>;
};

type ThrowAnimation = {
  targetId: string;
  objectId: string;
  impact: "split" | "blink";
  phase: "flying" | "impact";
  origin: WorldPoint;
};

type ThrowPresentation = Omit<ThrowAnimation, "phase"> & {
  actionSucceeded: boolean;
  impactReady: boolean;
};

const OFFICE_ASSET_BASE = "/office-assets";
const WORLD_BOUNDS = { minX: -10.25, maxX: 10.25, minY: -6.25, maxY: 6.25 };
const BASE_WORLD_CHARACTER_SIZE = 2.1;
const WORLD_CHARACTER_SIZE = 1.8;
const CHARACTER_SCALE = WORLD_CHARACTER_SIZE / BASE_WORLD_CHARACTER_SIZE;
const PLAYER_RADIUS = 0.45 * CHARACTER_SCALE;
const PLAYER_SPEED = 4;
const INTERACTION_DISTANCE = 2.25;

const npcWorldLayout: Record<string, { point: WorldPoint; location: string; asset: string }> = {
  backend_01: { point: { x: -6, y: 1.06 }, location: "dev_area", asset: `${OFFICE_ASSET_BASE}/characters/backend.png` },
  frontend_01: { point: { x: -6, y: -1.54 }, location: "dev_area", asset: `${OFFICE_ASSET_BASE}/characters/frontend.png` },
  qa_01: { point: { x: 5, y: 1.81 }, location: "qa_desk", asset: `${OFFICE_ASSET_BASE}/characters/qa.png` },
  pm_01: { point: { x: 5, y: -4.69 }, location: "pm_desk", asset: `${OFFICE_ASSET_BASE}/characters/pm.png` },
};

const locationSpawnPoints: Record<string, WorldPoint> = {
  meeting_room: { x: 0, y: -0.5 },
  dev_area: { x: -2.5, y: -0.5 },
  qa_desk: { x: 2.5, y: 1.1 },
  pm_desk: { x: 2.5, y: -1.2 },
};

const droppedObjectPoints: Record<string, WorldPoint> = {
  dev_area: { x: -4.25, y: -2.95 },
  qa_desk: { x: 3.65, y: 1.35 },
  pm_desk: { x: 3.65, y: -4.25 },
  meeting_room: { x: 0, y: -2.1 },
};

const deskFixtures = [
  { id: "backend-desk", point: { x: -6, y: 2.5 }, objectId: "backend_keyboard" },
  { id: "frontend-desk", point: { x: -6, y: -0.1 }, objectId: "frontend_keyboard" },
  { id: "shared-desk", point: { x: -6, y: -2.7 }, objectId: null },
  { id: "qa-desk", point: { x: 5, y: 3.25 }, objectId: "qa_keyboard" },
  { id: "pm-desk", point: { x: 5, y: -3.25 }, objectId: "pm_keyboard" },
];

const collisionRects = [
  { x: 0, y: 6.1, width: 20.5, height: 0.35 },
  { x: 0, y: -6.1, width: 20.5, height: 0.35 },
  { x: -10.1, y: 0, width: 0.35, height: 12 },
  { x: 10.1, y: 0, width: 0.35, height: 12 },
  { x: -6, y: 2.5, width: 3.9, height: 0.75 },
  { x: -6, y: -0.1, width: 3.9, height: 0.75 },
  { x: -6, y: -2.7, width: 3.9, height: 0.75 },
  { x: 5, y: 3.25, width: 3.9, height: 0.75 },
  { x: 5, y: -3.25, width: 3.9, height: 0.75 },
  { x: -9.25, y: 0, width: 0.7, height: 2 },
  { x: -1.7, y: 4.75, width: 0.85, height: 1.35 },
  { x: 8.7, y: 0.2, width: 0.7, height: 0.75 },
];

const directionBackgroundPositions: Record<SpriteDirection, string> = {
  front: "0% 0%",
  back: "33.333% 0%",
  left: "66.667% 0%",
  right: "100% 0%",
};

const locationLabels: Record<string, string> = {
  meeting_room: "MEETING ROOM",
  dev_area: "DEV AREA",
  qa_desk: "QA DESK",
  pm_desk: "PM DESK",
};

export function ModeChooser({ snapshot, onChoose }: { snapshot: GameSnapshot; onChoose: (mode: VisualMode) => void }) {
  return (
    <main className="mode-chooser-shell">
      <header className="chooser-header">
        <div className="brand-lockup">
          <div className="brand-mark">WM</div>
          <div>
            <h1>WHO MESSED UP?</h1>
            <p>AI OFFICE INCIDENT SIMULATOR</p>
          </div>
        </div>
        <span className="chooser-session">SESSION READY · {snapshot.ai_provider}</span>
      </header>

      <section className="mode-chooser-content">
        <div className="chooser-copy">
          <span className="chooser-kicker">INCIDENT 01 / SELECT INTERFACE</span>
          <h2>어떻게 사건을<br /><em>조사할까요?</em></h2>
          <p>같은 사건 상태를 두 가지 방식으로 플레이할 수 있습니다. 대화와 증거에 집중하거나, Unity에서 만든 사무실 공간을 직접 돌아다녀 보세요.</p>
          <div className="chooser-line" />
          <span className="chooser-hint">게임 화면에서는 WASD / 방향키로 이동하고, 가까이 가서 E를 누르면 상호작용합니다.</span>
        </div>

        <div className="mode-choice-grid">
          <button className="mode-choice dialogue-choice" type="button" onClick={() => onChoose("dialogue")}>
            <span className="mode-choice-index">01</span>
            <span className="mode-choice-title">DIALOGUE MODE</span>
            <span className="mode-choice-description">텍스트 로그, NPC 상태, Agent trace를 한 화면에서 확인합니다.</span>
            <span className="mode-choice-action">START CONVERSATION <b>↗</b></span>
          </button>

          <button className="mode-choice visual-choice" type="button" onClick={() => onChoose("visual")}>
            <div className="chooser-art-map" aria-hidden="true">
              <div className="chooser-art-zone chooser-art-dev" />
              <div className="chooser-art-zone chooser-art-qa" />
              <div className="chooser-art-zone chooser-art-pm" />
              <div className="chooser-art-desk chooser-art-desk-a" />
              <div className="chooser-art-desk chooser-art-desk-b" />
              <CharacterSprite asset={`${OFFICE_ASSET_BASE}/characters/player.png`} direction="front" className="chooser-player" />
              <CharacterSprite asset={`${OFFICE_ASSET_BASE}/characters/qa.png`} direction="back" className="chooser-qa" />
            </div>
            <span className="mode-choice-index">02</span>
            <span className="mode-choice-title">VISUAL OFFICE</span>
            <span className="mode-choice-description">Unity 오피스를 직접 이동하며 NPC와 오브젝트를 조사합니다.</span>
            <span className="mode-choice-action">ENTER THE OFFICE <b>↗</b></span>
          </button>
        </div>
      </section>
    </main>
  );
}

export function VisualOffice({
  snapshot,
  selectedNpcId,
  submitting,
  error,
  actionAlert,
  onChooseMode,
  onReset,
  onLocationChange,
  onSelectNpc,
  dialogue,
  onAction,
}: VisualOfficeProps) {
  const initialPosition = locationSpawnPoints[snapshot.current_location] ?? locationSpawnPoints.meeting_room;
  const [playerPosition, setPlayerPosition] = useState<WorldPoint>(initialPosition);
  const [playerDirection, setPlayerDirection] = useState<SpriteDirection>("front");
  const [interactionOpen, setInteractionOpen] = useState(false);
  const [actionMenuOpen, setActionMenuOpen] = useState(false);
  const [playerActionOpen, setPlayerActionOpen] = useState(false);
  const [selectedThrowObjectId, setSelectedThrowObjectId] = useState<string | null>(null);
  const [inventoryOpen, setInventoryOpen] = useState(false);
  const [throwAnimation, setThrowAnimation] = useState<ThrowAnimation | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);
  const directionPressRef = useRef<{ time: number; position: WorldPoint } | null>(null);
  const nearestNpcRef = useRef<typeof nearestNpc>(null);
  const lastLocationRef = useRef(getLocationForPoint(initialPosition));
  const throwTimersRef = useRef<number[]>([]);
  const throwPresentationRef = useRef<ThrowPresentation | null>(null);

  const selectedNpc = useMemo(
    () => snapshot.npcs.find((npc) => npc.id === selectedNpcId) ?? snapshot.npcs[0] ?? null,
    [selectedNpcId, snapshot.npcs],
  );
  const nearestNpc = useMemo(() => {
    const candidates = snapshot.npcs
      .map((npc) => {
        const layout = npcWorldLayout[npc.id];
        if (!layout) return null;
        const distance = Math.hypot(layout.point.x - playerPosition.x, layout.point.y - playerPosition.y);
        return { npc, layout, distance };
      })
      .filter((candidate): candidate is { npc: NPCState; layout: (typeof npcWorldLayout)[string]; distance: number } => candidate !== null)
      .sort((a, b) => a.distance - b.distance)[0];
    return candidates && candidates.distance <= INTERACTION_DISTANCE ? candidates : null;
  }, [playerPosition, snapshot.npcs]);
  nearestNpcRef.current = nearestNpc;
  const currentLocation = getLocationForPoint(playerPosition);
  const heldObjects = useMemo(
    () => snapshot.player_inventory.held_object_ids
      .map((objectId) => snapshot.world_objects.find((worldObject) => worldObject.id === objectId))
      .filter((worldObject): worldObject is GameSnapshot["world_objects"][number] => Boolean(worldObject && worldObject.condition !== "destroyed")),
    [snapshot.player_inventory.held_object_ids, snapshot.world_objects],
  );
  const nearbyActions = useMemo(() => {
    if (!nearestNpc) return [];
    return snapshot.available_game_actions.filter((action) => action.scope === "held_item" || action.target_id === nearestNpc.npc.id);
  }, [nearestNpc, snapshot.available_game_actions]);
  const targetActions = nearbyActions.filter((action) => action.scope !== "held_item");
  const heldItemActions = nearbyActions.filter((action) => action.scope === "held_item");
  const globalThrowActions = useMemo(
    () => snapshot.available_game_actions.filter((action) => action.family === "throw_held_object" && action.enabled),
    [snapshot.available_game_actions],
  );
  const throwObjectIds = useMemo(
    () => [...new Set(globalThrowActions.map((action) => action.object_id).filter((objectId): objectId is string => Boolean(objectId)))],
    [globalThrowActions],
  );
  const selectedThrowActions = useMemo(
    () => globalThrowActions.filter((action) => action.object_id === selectedThrowObjectId),
    [globalThrowActions, selectedThrowObjectId],
  );
  const droppedObjects = snapshot.world_objects.filter(
    (worldObject) => worldObject.is_dropped && worldObject.holder_id === null && worldObject.condition !== "destroyed",
  );
  const latestEvent = snapshot.events.slice(-1)[0] ?? null;
  const isNpcVisuallyComatose = (npc: NPCState) => (
    npc.physical_state === "comatose"
    && !(throwAnimation?.targetId === npc.id && throwAnimation.phase === "flying")
  );

  useEffect(() => {
    if (currentLocation === lastLocationRef.current) return;
    lastLocationRef.current = currentLocation;
    onLocationChange(currentLocation);
  }, [currentLocation, onLocationChange]);

  const keysRef = useGameKeyboard({
    movementBlocked: dialogue.state.isOpen,
    completed: snapshot.completed,
    onInteract: () => {
      const nearbyNpc = nearestNpcRef.current;
      if (!nearbyNpc) return false;
      onSelectNpc(nearbyNpc.npc.id);
      setInteractionOpen(true);
      return true;
    },
    onInventory: () => setInventoryOpen(open => !open),
  });

  useLayoutEffect(() => { dialogue.setNearby(nearestNpc?.npc.id ?? null); }, [nearestNpc?.npc.id, dialogue.setNearby]);
  useEffect(() => () => dialogue.setNearby(null), [dialogue.setNearby]);

  const openDialogue = (npcId: string) => {
    keysRef.current.clear();
    setInteractionOpen(false);
    setActionMenuOpen(false);
    setPlayerActionOpen(false);
    setSelectedThrowObjectId(null);
    dialogue.open(npcId);
  };
  const closeDialogue = () => {
    dialogue.close();
    setInteractionOpen(Boolean(nearestNpcRef.current));
    keysRef.current.clear();
    requestAnimationFrame(() => mapRef.current?.focus({ preventScroll: true }));
  };

  useEffect(() => {
    if (snapshot.completed || dialogue.state.isOpen) return;
    let animationFrame = 0;
    let lastTime = performance.now();

    const tick = (time: number) => {
      const delta = Math.min((time - lastTime) / 1000, 0.05);
      lastTime = time;
      const keys = keysRef.current;
      let x = 0;
      let y = 0;
      if (keys.has("a") || keys.has("arrowleft")) x -= 1;
      if (keys.has("d") || keys.has("arrowright")) x += 1;
      if (keys.has("s") || keys.has("arrowdown")) y -= 1;
      if (keys.has("w") || keys.has("arrowup")) y += 1;
      if (x !== 0 || y !== 0) {
        const length = Math.hypot(x, y);
        const next = { x: x / length, y: y / length };
        setPlayerDirection(
          Math.abs(next.x) > Math.abs(next.y)
            ? (next.x < 0 ? "left" : "right")
            : (next.y > 0 ? "back" : "front"),
        );
        setPlayerPosition((position) => movePlayer(position, { x: next.x * PLAYER_SPEED * delta, y: next.y * PLAYER_SPEED * delta }));
      }
      animationFrame = requestAnimationFrame(tick);
    };

    animationFrame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animationFrame);
  }, [snapshot.completed, dialogue.state.isOpen]);

  useEffect(() => {
    if (!nearestNpc) {
      setInteractionOpen(false);
      setActionMenuOpen(false);
    }
  }, [nearestNpc]);

  useEffect(() => {
    if (playerActionOpen && throwObjectIds.length === 0) {
      setPlayerActionOpen(false);
      setSelectedThrowObjectId(null);
    }
  }, [playerActionOpen, throwObjectIds.length]);

  useEffect(() => () => {
    throwTimersRef.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const nudgePlayer = (key: "arrowup" | "arrowleft" | "arrowdown" | "arrowright", startedAt?: WorldPoint) => {
    if (dialogue.state.isOpen || snapshot.completed) return;
    const direction = { arrowup: { x: 0, y: 1 }, arrowleft: { x: -1, y: 0 }, arrowdown: { x: 0, y: -1 }, arrowright: { x: 1, y: 0 } }[key];
    setPlayerDirection(direction.x ? (direction.x < 0 ? "left" : "right") : (direction.y > 0 ? "back" : "front"));
    setPlayerPosition(position => {
      const travelled = startedAt ? Math.hypot(position.x - startedAt.x, position.y - startedAt.y) : 0;
      const distance = Math.max(0, PLAYER_SPEED * 0.1 - travelled);
      return movePlayer(position, { x: direction.x * distance, y: direction.y * distance });
    });
  };

  const directionControls = (key: "arrowup" | "arrowleft" | "arrowdown" | "arrowright") => ({
    disabled: dialogue.state.isOpen || snapshot.completed,
    onPointerDown: () => { directionPressRef.current = { time: performance.now(), position: playerPosition }; keysRef.current.add(key); },
    onPointerUp: () => keysRef.current.delete(key),
    onPointerLeave: () => { keysRef.current.delete(key); directionPressRef.current = null; },
    onPointerCancel: () => { keysRef.current.clear(); directionPressRef.current = null; },
    onClick: () => {
      const press = directionPressRef.current;
      directionPressRef.current = null;
      if (!press || performance.now() - press.time < 150) nudgePlayer(key, press?.position);
    },
  });

  const selectNpc = (id: string) => {
    onSelectNpc(id);
    const layout = npcWorldLayout[id];
    if (layout) {
      const distance = Math.hypot(layout.point.x - playerPosition.x, layout.point.y - playerPosition.y);
      setInteractionOpen(distance <= INTERACTION_DISTANCE);
    }
  };

  const executeVisualAction = async (action: AvailableGameAction) => {
    setActionMenuOpen(false);
    setPlayerActionOpen(false);
    setSelectedThrowObjectId(null);
    const isThrowAction = action.family === "throw_held_object" && action.object_id && action.target_id;
    if (isThrowAction && action.target_id && action.object_id) {
      startThrowAnimation(action);
    }
    const succeeded = await onAction(action);
    if (isThrowAction) {
      if (succeeded) {
        completeThrowAction();
      } else {
        cancelThrowAnimation();
      }
    }
  };

  const startThrowAnimation = (action: AvailableGameAction) => {
    if (!action.target_id || !action.object_id) return;
    const object = snapshot.world_objects.find((worldObject) => worldObject.id === action.object_id);
    const impact = isPersonObjectId(action.object_id)
      ? "blink"
      : object?.throw_impact ?? (object?.throw_effect === "support" ? "blink" : "split");
    const targetId = action.target_id;
    const origin = { ...playerPosition };
    cancelThrowAnimation();
    throwPresentationRef.current = { targetId, objectId: action.object_id, impact, origin, actionSucceeded: false, impactReady: false };
    setThrowAnimation({ targetId, objectId: action.object_id, impact, origin, phase: "flying" });
    throwTimersRef.current = [window.setTimeout(() => {
      const current = throwPresentationRef.current;
      if (!current || current.targetId !== targetId) return;
      current.impactReady = true;
      triggerThrowImpact(targetId);
    }, 1360)];
  };

  const completeThrowAction = () => {
    const current = throwPresentationRef.current;
    if (!current) return;
    current.actionSucceeded = true;
    triggerThrowImpact(current.targetId);
  };

  const triggerThrowImpact = (targetId: string) => {
    const current = throwPresentationRef.current;
    if (!current || current.targetId !== targetId || !current.actionSucceeded || !current.impactReady) return;
    setThrowAnimation({ targetId, objectId: current.objectId, impact: current.impact, origin: current.origin, phase: "impact" });
    throwTimersRef.current.push(window.setTimeout(() => {
      if (throwPresentationRef.current?.targetId !== targetId) return;
      throwPresentationRef.current = null;
      setThrowAnimation(null);
    }, 480));
  };

  const cancelThrowAnimation = () => {
    throwTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    throwTimersRef.current = [];
    throwPresentationRef.current = null;
    setThrowAnimation(null);
  };

  const openPlayerActionMenu = () => {
    setInteractionOpen(false);
    setActionMenuOpen(false);
    setSelectedThrowObjectId(null);
    setPlayerActionOpen(true);
  };

  return (
    <main className="visual-office-shell">
      <header className="visual-topbar">
        <div className="brand-lockup">
          <div className="brand-mark">WM</div>
          <div>
            <h1>WHO MESSED UP?</h1>
            <p>VISUAL OFFICE INCIDENT SIMULATOR</p>
          </div>
        </div>
        <div className="visual-topbar-meta">
          <span className="visual-provider-dot" />
          <span>{snapshot.ai_provider} / {snapshot.ai_model}</span>
          <span>TURN {String(snapshot.turn).padStart(2, "0")} / 20</span>
          <span className={`visual-incident-state ${snapshot.incident_status.toLowerCase()}`}>{snapshot.incident_status}</span>
          <div className="view-switch visual-view-switch" aria-label="화면 모드 선택">
            <button type="button" onClick={() => onChooseMode("dialogue")}>DIALOGUE</button>
            <button className="active" type="button" onClick={() => onChooseMode("visual")}>OFFICE VIEW</button>
          </div>
          <button className="visual-reset-button" type="button" onClick={onReset} disabled={submitting}>RESET</button>
        </div>
      </header>

      <section className="visual-workspace">
        <div className="visual-main-column">
          <div className="visual-stage-heading">
            <div>
              <span className="visual-overline">LIVE WORLD / UNITY ASSET BRIDGE</span>
              <h2>{locationLabels[currentLocation] ?? currentLocation.replaceAll("_", " ")}</h2>
            </div>
            <div className="visual-stage-stats">
              <span><i className="status-light" /> 서버 연결됨</span>
              <span>{snapshot.events.length} EVENTS</span>
            </div>
          </div>

          <div className="visual-stage-frame">
            <div className="office-map" role="application" aria-label="Unity 스타일 사무실 게임 화면" tabIndex={0} ref={mapRef}>
              <div className="map-floor" />
              <div className="map-zone map-dev-zone"><span>DEV AREA</span></div>
              <div className="map-zone map-qa-zone"><span>QA DESK</span></div>
              <div className="map-zone map-pm-zone"><span>PM DESK</span></div>

              <div className="map-wall map-wall-north" />
              <div className="map-wall map-wall-south" />
              <div className="map-wall map-wall-west" />
              <div className="map-wall map-wall-east" />
              <MapAsset src={`${OFFICE_ASSET_BASE}/partition.png`} alt="" className="map-partition partition-dev-top" style={centeredWorldStyle(-8.2, 4.65, 2.4, 0.62)} />
              <MapAsset src={`${OFFICE_ASSET_BASE}/partition.png`} alt="" className="map-partition partition-dev-bottom" style={centeredWorldStyle(-8.2, -4.65, 2.4, 0.62)} />
              <MapAsset src={`${OFFICE_ASSET_BASE}/partition.png`} alt="" className="map-partition partition-qa" style={centeredWorldStyle(5, 5.15, 4.5, 0.62)} />
              <MapAsset src={`${OFFICE_ASSET_BASE}/partition.png`} alt="" className="map-partition partition-pm" style={centeredWorldStyle(5, -5.15, 4.5, 0.62)} />

              {deskFixtures.map((desk) => {
                const keyboard = desk.objectId ? snapshot.world_objects.find((item) => item.id === desk.objectId) : null;
                const keyboardHidden = keyboard?.holder_id === "player" || keyboard?.condition === "destroyed" || keyboard?.is_dropped;
                return (
                  <div className="desk-fixture" key={desk.id}>
                    <MapAsset src={`${OFFICE_ASSET_BASE}/desk.png`} alt="" className="map-desk" style={centeredWorldStyle(desk.point.x, desk.point.y, 5, 1.7)} />
                    <MapAsset src={`${OFFICE_ASSET_BASE}/monitor.png`} alt="" className="map-monitor" style={centeredWorldStyle(desk.point.x, desk.point.y + 0.56, 1.7, 1.1)} />
                    {!keyboardHidden && <MapAsset src={`${OFFICE_ASSET_BASE}/keyboard.png`} alt="" className={`map-keyboard ${keyboard?.condition ?? "normal"}`} style={centeredWorldStyle(desk.point.x, desk.point.y - 0.4, 1.3, 0.48)} />}
                  </div>
                );
              })}

              {droppedObjects.map((worldObject) => {
                const point = droppedObjectPoints[worldObject.location] ?? droppedObjectPoints.meeting_room;
                return (
                  <div key={`dropped-${worldObject.id}`} className={`dropped-world-object ${isPersonObjectId(worldObject.id) ? "dropped-person-object" : ""}`} style={centeredWorldStyle(point.x, point.y, 1.25, 0.46)} aria-label={`${worldObject.name} 바닥에 놓임`}>
                    <WorldObjectVisual objectId={worldObject.id} />
                  </div>
                );
              })}

              <MapAsset src={`${OFFICE_ASSET_BASE}/server_rack.png`} alt="server rack" className="map-decoration" style={centeredWorldStyle(-9.25, 0, 0.7, 2)} />
              <MapAsset src={`${OFFICE_ASSET_BASE}/whiteboard.png`} alt="whiteboard" className="map-decoration" style={centeredWorldStyle(-1.7, 4.75, 0.85, 1.35)} />
              <MapAsset src={`${OFFICE_ASSET_BASE}/coffee_machine.png`} alt="coffee machine" className="map-decoration" style={centeredWorldStyle(8.7, 0.2, 0.7, 0.75)} />

              {snapshot.npcs.map((npc) => {
                const layout = npcWorldLayout[npc.id];
                if (!layout) return null;
                const isSelected = npc.id === selectedNpcId;
                const isNearby = npc.id === nearestNpc?.npc.id;
                const isComatose = isNpcVisuallyComatose(npc);
                return (
                  <button
                    className={`world-character-button ${isSelected ? "selected" : ""} ${isNearby ? "nearby" : ""} ${isComatose ? "fallen" : ""} ${isFearOrShock(npc.dynamic_state.emotion) ? "shaking" : ""}`}
                    key={npc.id}
                    type="button"
                    style={worldPointStyle(layout.point, WORLD_CHARACTER_SIZE)}
                    onClick={() => selectNpc(npc.id)}
                    aria-label={`${npc.name} 선택`}
                    aria-describedby={`npc-status-${npc.id}`}
                    disabled={false}
                  >
                    <CharacterSprite asset={layout.asset} direction={isComatose ? "front" : "back"} className={isFearOrShock(npc.dynamic_state.emotion) ? "fear-shake" : ""} />
                    <span className="world-character-caption">
                      <span id={`npc-status-${npc.id}`} className={`world-character-status${isComatose ? " is-comatose" : ""}`}>
                        {isComatose ? "혼수상태" : formatEmotion(npc.dynamic_state.emotion)}
                      </span>
                      <span className="world-character-label">{npc.name}</span>
                    </span>
                    <span className={`world-emotion-dot ${npc.dynamic_state.emotion}`} />
                  </button>
                );
              })}

              {nearestNpc && (
                <div className="nearby-marker" style={worldPointStyle(nearestNpc.layout.point, 0.7)} aria-hidden="true">E</div>
              )}

              {interactionOpen && nearestNpc && !dialogue.state.isOpen && (
                <div className={`world-interaction-card ${actionMenuOpen ? "actions-open" : ""}`} style={centeredWorldStyle(nearestNpc.layout.point.x, nearestNpc.layout.point.y + 1.6, 3.65, actionMenuOpen ? 4.35 : 1.22)}>
                  <strong>{nearestNpc.npc.name}</strong>
                  {!actionMenuOpen ? (
                    <>
                      <span>무엇을 할까요?</span>
                      <div>
                        <button type="button" onClick={() => openDialogue(nearestNpc.npc.id)}>대화하기</button>
                        <button type="button" onClick={() => setActionMenuOpen(true)}>액션 보기</button>
                      </div>
                    </>
                  ) : (
                    <div className="world-action-menu">
                      {targetActions.length > 0 && <WorldActionGroup title={`${nearestNpc.npc.name} 관련`} actions={targetActions} submitting={submitting} onAction={(action) => void executeVisualAction(action)} />}
                      {heldItemActions.length > 0 && <WorldActionGroup title="손에 든 물건" actions={heldItemActions} submitting={submitting} onAction={(action) => void executeVisualAction(action)} />}
                      {nearbyActions.length === 0 && <span className="world-action-empty">현재 가능한 액션이 없습니다.</span>}
                      <button className="world-action-back" type="button" onClick={() => setActionMenuOpen(false)}>뒤로</button>
                    </div>
                  )}
                </div>
              )}

              <div className="world-player" style={worldPointStyle(playerPosition, WORLD_CHARACTER_SIZE)}>
                <CharacterSprite asset={`${OFFICE_ASSET_BASE}/characters/player.png`} direction={playerDirection} />
                <span className="world-character-label">PLAYER</span>
              </div>

              {throwAnimation && (() => {
                const target = npcWorldLayout[throwAnimation.targetId]?.point;
                if (!target) return null;
                const targetStyle = worldPointStyle(target, 1.2) as React.CSSProperties & Record<string, string>;
                const isPersonThrow = isPersonObjectId(throwAnimation.objectId);
                const throwWidth = isPersonThrow ? WORLD_CHARACTER_SIZE : 1.25;
                const impactWidth = isPersonThrow ? WORLD_CHARACTER_SIZE : 1.5;
                const objectName = snapshot.world_objects.find(object => object.id === throwAnimation.objectId)?.name ?? "물건";
                return throwAnimation.phase === "flying" ? (
                  <div
                    className={`thrown-world-object${isPersonThrow ? " thrown-person-object" : ""}`}
                    role="img"
                    aria-label={`${objectName} 투척 중`}
                    style={{
                      ...worldPointStyle(throwAnimation.origin, throwWidth),
                      "--throw-target-left": targetStyle.left,
                      "--throw-target-bottom": targetStyle.bottom,
                    } as React.CSSProperties}
                  >
                    {isPersonThrow ? (
                      <span className="thrown-person-spin"><WorldObjectVisual objectId={throwAnimation.objectId} /></span>
                    ) : <WorldObjectVisual objectId={throwAnimation.objectId} />}
                  </div>
                ) : (
                  isPersonThrow || throwAnimation.impact === "blink" ? (
                    <div className="world-blink-effect" style={worldPointStyle(target, impactWidth)} aria-label="긍정 아이템 점멸 효과">
                      <WorldObjectVisual objectId={throwAnimation.objectId} />
                    </div>
                  ) : (
                    <div className="world-break-effect" style={worldPointStyle(target, impactWidth)} aria-label="투척물 파손 효과">
                      <WorldObjectVisual objectId={throwAnimation.objectId} className="break-half break-left" />
                      <WorldObjectVisual objectId={throwAnimation.objectId} className="break-half break-right" />
                    </div>
                  )
                );
              })()}

              <div className="map-location-chip">{locationLabels[currentLocation] ?? currentLocation}</div>
            </div>
            <GameDialoguePanel controller={dialogue} snapshot={snapshot} requestBusy={submitting} viewportRef={mapRef} onClose={closeDialogue} />
            <div className="world-hud-overlay">
              {throwObjectIds.length > 0 && !playerActionOpen && !dialogue.state.isOpen && !actionMenuOpen && (
                <button className="world-player-action-button" type="button" style={worldPointStyle({ x: playerPosition.x, y: playerPosition.y + 1.28 }, 1.2)} onClick={openPlayerActionMenu} disabled={submitting || snapshot.completed}>
                  액션
                </button>
              )}

              {playerActionOpen && !dialogue.state.isOpen && !actionMenuOpen && (
                <div className="player-action-panel">
                  <div className="player-action-heading">
                    <strong>{selectedThrowObjectId ? "대상 선택" : "던질 물건 선택"}</strong>
                    <button type="button" onClick={() => { setPlayerActionOpen(false); setSelectedThrowObjectId(null); }}>닫기</button>
                  </div>
                  {!selectedThrowObjectId ? (
                    <div className="player-action-list">
                      {throwObjectIds.map((objectId) => {
                        const worldObject = snapshot.world_objects.find((item) => item.id === objectId);
                        return <button key={objectId} type="button" onClick={() => setSelectedThrowObjectId(objectId)}><span className="player-action-item-name">{worldObject ? formatWorldObjectName(worldObject.name) : objectId}</span> 던지기</button>;
                      })}
                    </div>
                  ) : (
                    <div className="player-action-list">
                      <button className="player-action-back" type="button" onClick={() => setSelectedThrowObjectId(null)}>← 물건 다시 선택</button>
                      {selectedThrowActions.map((action) => {
                        const target = snapshot.npcs.find((npc) => npc.id === action.target_id);
                        if (!target || isNpcVisuallyComatose(target)) return null;
                        return <button key={action.id} type="button" onClick={() => void executeVisualAction(action)} disabled={submitting}>{target.name} · {target.role}</button>;
                      })}
                    </div>
                  )}
                </div>
              )}

            </div>
          </div>

          <div className="visual-controls-bar">
            <div className="key-hints">
              <span><b>WASD</b> / <b>←↑↓→</b> MOVE</span>
              <span><b>E</b> INTERACT</span>
              <span><b>I</b> INVENTORY</span>
            </div>
            <div className="d-pad" aria-label="이동 방향 버튼">
              <button type="button" {...directionControls("arrowup")} aria-label="위로 이동">↑</button>
              <button type="button" {...directionControls("arrowleft")} aria-label="왼쪽으로 이동">←</button>
              <button type="button" {...directionControls("arrowdown")} aria-label="아래로 이동">↓</button>
              <button type="button" {...directionControls("arrowright")} aria-label="오른쪽으로 이동">→</button>
            </div>
          </div>
        </div>

        <aside className="visual-sidebar">
          <section className="visual-panel objective-panel">
            <div className="visual-panel-heading"><span>OBJECTIVE</span><span className="panel-count">{snapshot.turn}/20</span></div>
            <ul className="visual-objective-list">
              {snapshot.objective.map((item, index) => <li key={item}><span>0{index + 1}</span>{item}</li>)}
            </ul>
          </section>

          <section className="visual-panel team-panel">
            <div className="visual-panel-heading"><span>TEAM MEMBERS</span><span className="panel-count">{snapshot.npcs.length} ACTIVE</span></div>
            <div className="visual-team-list">
              {snapshot.npcs.map((npc) => (
                <button className={`${npc.id === selectedNpcId ? "selected" : ""} ${isNpcVisuallyComatose(npc) ? "fallen" : ""}`} type="button" key={npc.id} onClick={() => selectNpc(npc.id)}>
                  <span className="team-avatar"><CharacterSprite asset={npcWorldLayout[npc.id]?.asset ?? ""} direction="back" className={isFearOrShock(npc.dynamic_state.emotion) ? "fear-shake" : ""} /></span>
                  <span><strong>{npc.name}</strong><small>{isNpcVisuallyComatose(npc) ? "혼수상태" : npc.role}</small></span>
                  <i className={`team-presence ${npc.dynamic_state.emotion}`} />
                </button>
              ))}
            </div>
          </section>

          {selectedNpc && (
            <section className="visual-panel selected-panel">
              <div className="visual-panel-heading"><span>SELECTED NPC</span><span className="selected-state">{isNpcVisuallyComatose(selectedNpc) ? "혼수상태" : formatEmotion(selectedNpc.dynamic_state.emotion)}</span></div>
              <div className="visual-selected-person"><CharacterSprite asset={npcWorldLayout[selectedNpc.id]?.asset ?? ""} direction="back" className={isFearOrShock(selectedNpc.dynamic_state.emotion) ? "fear-shake" : ""} /><div><strong>{selectedNpc.name}</strong><small>{selectedNpc.role}</small></div></div>
              <div className="visual-metrics">
                <MetricBar label="STRESS" value={selectedNpc.dynamic_state.stress} tone="amber" />
                <MetricBar label="TRUST" value={Math.max(0, selectedNpc.dynamic_state.trust_toward_player)} tone="cyan" />
                <MetricBar label="COOPERATION" value={selectedNpc.dynamic_state.cooperation} tone="green" />
              </div>
              {nearestNpc?.npc.id === selectedNpc.id ? (
                <button className="visual-interact-button" type="button" onClick={() => setInteractionOpen(true)} disabled={submitting || snapshot.completed || dialogue.state.isOpen}>E · INTERACT</button>
              ) : (
                <p className="visual-distance-note">{isNpcVisuallyComatose(selectedNpc) ? "혼수상태로 대화할 수 없지만 물건과 액션은 상호작용할 수 있습니다." : "NPC에게 가까이 가면 상호작용할 수 있습니다."}</p>
              )}
            </section>
          )}

          <section className="visual-panel inventory-panel">
            <button className="visual-panel-heading inventory-heading" type="button" onClick={() => setInventoryOpen((open) => !open)}><span>INVENTORY · I</span><span>{inventoryOpen ? "−" : "+"}</span></button>
            {inventoryOpen && (
              <div className="visual-inventory-content">
                <span className="inventory-label">PLAYER HAND · {snapshot.player_inventory.unlimited ? "UNLIMITED" : `${heldObjects.length}/${snapshot.player_inventory.max_held_objects}`}</span>
                {heldObjects.length > 0 ? (
                  <div className="inventory-held-list">
                    {heldObjects.map((worldObject) => (
                      <p className="inventory-held" key={worldObject.id}><span className="inventory-held-name">{formatWorldObjectName(worldObject.name)}</span><small>{worldObject.throw_effect === "support" ? "positive" : "negative"}</small></p>
                    ))}
                  </div>
                ) : <p className="inventory-empty">Hands are empty.</p>}
                <span className="inventory-label">EVIDENCE</span>
                <p className="inventory-evidence-count">{snapshot.evidences.filter((evidence) => evidence.discovered).length} / {snapshot.evidences.length} discovered</p>
              </div>
            )}
          </section>

          {latestEvent && <div className="visual-event-toast"><span>LATEST EVENT · TURN {String(latestEvent.turn).padStart(2, "0")}</span><p>{latestEvent.message}</p></div>}
          {(error || actionAlert) && <div className="visual-alert" role="alert">{error ?? actionAlert}</div>}
        </aside>
      </section>
    </main>
  );
}

function CharacterSprite({ asset, direction, className = "" }: { asset: string; direction: SpriteDirection; className?: string }) {
  return <span className={`character-sprite ${className}`} style={{ backgroundImage: `url(${asset})`, backgroundPosition: directionBackgroundPositions[direction] }} aria-hidden="true" />;
}

function WorldObjectVisual({ objectId, className = "" }: { objectId: string; className?: string }) {
  if (isPersonObjectId(objectId)) {
    return <img className={`world-object-person ${className}`} src={getWorldObjectAsset(objectId)} alt="" draggable={false} />;
  }

  return <img className={className} src={getWorldObjectAsset(objectId)} alt="" draggable={false} />;
}

function getWorldObjectAsset(objectId: string): string {
  switch (objectId) {
    case "americano_coupon":
    case "department_store_voucher":
    case "luxury_handbag":
      return `${OFFICE_ASSET_BASE}/items/${objectId}.png`;
    case "representative_person":
      return `${OFFICE_ASSET_BASE}/items/representative_person.png`;
    case "team_leader_person":
      return `${OFFICE_ASSET_BASE}/items/team_leader_person.png`;
    case "division_head_person":
      return `${OFFICE_ASSET_BASE}/items/division_head_person.png`;
    default:
      return `${OFFICE_ASSET_BASE}/keyboard.png`;
  }
}

function isPersonObjectId(objectId: string): boolean {
  return ["representative_person", "team_leader_person", "division_head_person"].includes(objectId);
}

function isFearOrShock(emotion: string): boolean {
  return emotion === "afraid" || emotion === "shocked";
}

function MapAsset({ src, alt, className, style }: { src: string; alt: string; className: string; style: React.CSSProperties }) {
  return <img className={className} src={src} alt={alt} style={style} draggable={false} />;
}

function MetricBar({ label, value, tone }: { label: string; value: number; tone: "amber" | "cyan" | "green" }) {
  return (
    <div className="visual-metric-row">
      <div><span>{label}</span><strong>{value}</strong></div>
      <div className="metric-track"><span className={tone} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>
    </div>
  );
}

function WorldActionGroup({
  title,
  actions,
  submitting,
  onAction,
}: {
  title: string;
  actions: AvailableGameAction[];
  submitting: boolean;
  onAction: (action: AvailableGameAction) => void;
}) {
  return (
    <section className="world-action-group">
      <span>{title}</span>
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          disabled={submitting || !action.enabled}
          title={action.disabled_reason ?? action.id}
          onClick={() => onAction(action)}
        >
          <GameActionLabel action={action} />
        </button>
      ))}
    </section>
  );
}

function centeredWorldStyle(x: number, y: number, width: number, height: number): React.CSSProperties {
  return {
    left: `${((x - width / 2 - WORLD_BOUNDS.minX) / (WORLD_BOUNDS.maxX - WORLD_BOUNDS.minX)) * 100}%`,
    bottom: `${((y - height / 2 - WORLD_BOUNDS.minY) / (WORLD_BOUNDS.maxY - WORLD_BOUNDS.minY)) * 100}%`,
    width: `${(width / (WORLD_BOUNDS.maxX - WORLD_BOUNDS.minX)) * 100}%`,
    height: `${(height / (WORLD_BOUNDS.maxY - WORLD_BOUNDS.minY)) * 100}%`,
  };
}

function worldPointStyle(point: WorldPoint, width: number): React.CSSProperties {
  return {
    left: `${((point.x - WORLD_BOUNDS.minX) / (WORLD_BOUNDS.maxX - WORLD_BOUNDS.minX)) * 100}%`,
    bottom: `${((point.y - WORLD_BOUNDS.minY) / (WORLD_BOUNDS.maxY - WORLD_BOUNDS.minY)) * 100}%`,
    width: `${(width / (WORLD_BOUNDS.maxX - WORLD_BOUNDS.minX)) * 100}%`,
    height: `${(width / (WORLD_BOUNDS.maxX - WORLD_BOUNDS.minX)) * 100}%`,
  };
}

function getLocationForPoint(point: WorldPoint): string {
  if (point.x >= 2.25 && point.x <= 7.75 && point.y >= 0.95 && point.y <= 5.05) return "qa_desk";
  if (point.x >= 2.25 && point.x <= 7.75 && point.y >= -5.05 && point.y <= -0.95) return "pm_desk";
  if (point.x >= -9.1 && point.x <= -1.9 && point.y >= -5.15 && point.y <= 5.15) return "dev_area";
  if (point.x >= -2 && point.x <= 2 && point.y >= -6 && point.y <= 6) return "meeting_room";
  return point.x < -1.9 ? "dev_area" : point.y >= 0 ? "qa_desk" : "pm_desk";
}

function movePlayer(position: WorldPoint, delta: WorldPoint): WorldPoint {
  const candidateX = { x: position.x + delta.x, y: position.y };
  const candidateY = { x: position.x, y: position.y + delta.y };
  return {
    x: collides(candidateX) ? position.x : candidateX.x,
    y: collides(candidateY) ? position.y : candidateY.y,
  };
}

function collides(point: WorldPoint): boolean {
  if (
    point.x < WORLD_BOUNDS.minX + PLAYER_RADIUS
    || point.x > WORLD_BOUNDS.maxX - PLAYER_RADIUS
    || point.y < WORLD_BOUNDS.minY + PLAYER_RADIUS
    || point.y > WORLD_BOUNDS.maxY - PLAYER_RADIUS
  ) {
    return true;
  }

  return collisionRects.some((rect) => (
    point.x >= rect.x - rect.width / 2 - PLAYER_RADIUS
    && point.x <= rect.x + rect.width / 2 + PLAYER_RADIUS
    && point.y >= rect.y - rect.height / 2 - PLAYER_RADIUS
    && point.y <= rect.y + rect.height / 2 + PLAYER_RADIUS
  ));
}
