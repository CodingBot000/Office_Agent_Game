import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, getSession } from "../api";
import type { GameSnapshot } from "../types";

type RequestOptions = { replaceSession?: boolean; allowCompleted?: boolean };
export const REQUEST_TIMEOUT_MS = 120_000;

export function useSessionRequests(snapshot: GameSnapshot | null, onSnapshot: (snapshot: GameSnapshot) => void) {
  const current = useRef(snapshot);
  const apply = useRef(onSnapshot);
  const inflight = useRef<symbol | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  apply.current = onSnapshot;
  useLayoutEffect(() => { current.current = snapshot; }, [snapshot]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; activeController.current?.abort(); };
  }, []);
  const isBusy = useCallback(() => inflight.current !== null, []);

  const run = useCallback(async <T,>(
    operation: (sessionId: string, signal: AbortSignal) => Promise<T>,
    snapshotOf: (result: T) => GameSnapshot,
    options: RequestOptions = {},
  ): Promise<T> => {
    const source = current.current;
    if (!source) throw new Error("Backend 세션 준비 중입니다.");
    if (inflight.current) throw new Error("Backend 요청이 처리 중입니다.");
    if (source.completed && !options.allowCompleted) throw new Error("이미 종료된 사건입니다.");
    const token = Symbol("session-request");
    const controller = new AbortController();
    activeController.current = controller;
    inflight.current = token;
    setBusy(true);
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const accept = (next: GameSnapshot, replacing = false) => {
      if (!mounted.current || current.current?.session_id !== source.session_id) throw new Error("이전 세션의 응답은 적용하지 않았습니다.");
      if (!replacing && next.session_id !== source.session_id) throw new Error("다른 세션의 응답을 받았습니다.");
      if (!replacing && (next.revision ?? next.turn) < (current.current.revision ?? current.current.turn)) return;
      current.current = next;
      apply.current(next);
    };
    try {
      const result = await operation(source.session_id, controller.signal);
      accept(snapshotOf(result), options.replaceSession);
      return result;
    } catch (reason) {
      if (controller.signal.aborted) {
        throw new Error("응답 대기 시간이 초과됐습니다. 서버에서는 처리 중일 수 있으니 상태를 확인한 뒤 다시 시도해 주세요.");
      }
      if (reason instanceof ApiError && reason.status === 409 && current.current?.session_id === source.session_id) {
        try {
          accept(await getSession(source.session_id, controller.signal));
        } catch (reloadError) {
          if (reloadError instanceof ApiError && reloadError.status === 404) {
            throw new Error("이 세션은 다른 요청에서 초기화되었습니다. 페이지를 새로고침해 새 세션을 시작해 주세요.");
          }
          throw new Error("요청이 충돌했고 최신 상태를 불러오지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.");
        }
        throw new Error("다른 요청으로 세션이 변경되어 최신 상태를 불러왔습니다. 내용을 확인한 뒤 다시 시도해 주세요.");
      }
      throw reason;
    } finally {
      window.clearTimeout(timer);
      if (inflight.current === token) {
        inflight.current = null;
        activeController.current = null;
        if (mounted.current) setBusy(false);
      }
    }
  }, []);

  return { busy, isBusy, run };
}
