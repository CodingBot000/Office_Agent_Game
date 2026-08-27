import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getSession } from "../api";
import { snapshot, response, deferred } from "../test/fixtures";
import { REQUEST_TIMEOUT_MS, useSessionRequests } from "./useSessionRequests";

vi.mock("../api", async importOriginal => ({ ...await importOriginal<typeof import("../api")>(), getSession: vi.fn() }));
afterEach(() => vi.useRealTimers());

describe("shared session requests", () => {
  it("locks synchronously before React rerenders and accepts only one operation", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const apply = vi.fn();
    const current = snapshot();
    const { result } = renderHook(() => useSessionRequests(current, apply));
    let first!: Promise<ReturnType<typeof response>>;
    act(() => { first = result.current.run(() => pending.promise, result => result.snapshot); });
    expect(result.current.isBusy()).toBe(true);
    await expect(result.current.run(vi.fn(), () => current)).rejects.toThrow("요청이 처리 중");
    await act(async () => { pending.resolve(response()); await first; });
    expect(apply).toHaveBeenCalledOnce();
    expect(result.current.busy).toBe(false);
  });

  it("reloads a 409 snapshot without retrying the action", async () => {
    vi.mocked(getSession).mockResolvedValue(snapshot({ revision: 3, turn: 2 }));
    const apply = vi.fn();
    const operation = vi.fn().mockRejectedValue(new ApiError(409, "conflict"));
    const current = snapshot();
    const { result } = renderHook(() => useSessionRequests(current, apply));
    await act(async () => { await expect(result.current.run(operation, () => current)).rejects.toThrow("최신 상태"); });
    expect(operation).toHaveBeenCalledOnce();
    expect(apply.mock.calls[0][0].revision).toBe(3);
  });

  it("does not replace a newer session with a late response", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const apply = vi.fn();
    const { result, rerender } = renderHook(({ current }) => useSessionRequests(current, apply), { initialProps: { current: snapshot() } });
    let settled!: Promise<unknown>;
    act(() => { settled = result.current.run(() => pending.promise, item => item.snapshot).catch(reason => reason); });
    rerender({ current: snapshot({ session_id: "new-session" }) });
    await act(async () => { pending.resolve(response()); await settled; });
    expect(apply).not.toHaveBeenCalled();
  });

  it("aborts a timed out fetch and releases the lock without retrying", async () => {
    vi.useFakeTimers();
    const current = snapshot();
    const { result } = renderHook(() => useSessionRequests(current, vi.fn()));
    const operation = vi.fn((_id: string, signal: AbortSignal) => new Promise<ReturnType<typeof response>>((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    let settled!: Promise<unknown>;
    act(() => { settled = result.current.run(operation, value => value.snapshot).catch(reason => reason); });
    await act(async () => { await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS); });
    expect((await settled as Error).message).toContain("서버에서는 처리 중일 수");
    expect(result.current.isBusy()).toBe(false);
    expect(operation).toHaveBeenCalledOnce();
  });
});
