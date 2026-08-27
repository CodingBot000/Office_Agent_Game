import type { AvailableGameAction } from "./types";

const emotionLabels: Record<string, string> = {
  neutral: "중립",
  calm: "차분",
  tense: "긴장",
  worried: "걱정",
  guarded: "경계",
  urgent: "초조",
  defensive: "방어적",
  relieved: "안도",
  afraid: "두려움",
  shocked: "충격",
  angry: "분노",
  cautiously_relieved: "조심스러운 안도",
  supported: "지지받음",
  attentive: "주의 깊음",
  uneasy: "불안",
  focused: "집중",
  concerned: "우려",
};

export function formatEmotion(emotion: string): string {
  return emotionLabels[emotion] ?? "알 수 없음";
}

const specialWorldObjectNames: Record<string, string> = {
  americano_coupon: "아메리카노-쿠폰",
  department_store_voucher: "백화점-상품권",
  luxury_handbag: "명품-가방",
  representative_person: "대표님",
  team_leader_person: "팀장님",
  division_head_person: "본부장님",
};

export function formatWorldObjectName(name: string): string {
  return name.trim().replace(/\s+/g, "-");
}

function formatWorldObjectId(objectId: string): string {
  if (specialWorldObjectNames[objectId]) {
    return specialWorldObjectNames[objectId];
  }

  return objectId
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("-");
}

export function GameActionLabel({ action }: { action: AvailableGameAction }) {
  const objectName = action.object_id ? formatWorldObjectId(action.object_id) : null;
  if (!objectName) return <>{action.label}</>;

  const actionVerb = {
    pick_up_object: "Pick up",
    break_held_object: "Break",
    drop_held_object: "Drop",
    inspect_object: "Inspect",
    throw_held_object: "Throw",
  }[action.family];
  const targetLabel = action.family === "throw_held_object" ? action.label.split(" at ").slice(1).join(" at ") : "";

  return (
    <>
      <span>{actionVerb} </span>
      <strong className="game-action-object-name">{objectName}</strong>
      {targetLabel && <span> at {targetLabel}</span>}
    </>
  );
}
