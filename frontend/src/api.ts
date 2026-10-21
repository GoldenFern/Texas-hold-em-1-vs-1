import type {
  AnalysisResult,
  Bootstrap,
  GameSnapshot,
  MatchSetupPayload,
  AgentEvent,
} from "./types";

export class ApiError extends Error {
  status: number;
  kind?: string;
  constructor(message: string, status = 0, kind?: string) {
    super(message);
    this.status = status;
    this.kind = kind;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload: any = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const message = payload?.error || `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, response.status, payload?.kind);
  }
  return payload as T;
}

export const api = {
  bootstrap(): Promise<Bootstrap> {
    return request<Bootstrap>("/api/bootstrap");
  },
  state(): Promise<GameSnapshot> {
    return request<GameSnapshot>("/api/game/state");
  },
  transcripts(): Promise<{ match_id: string; seats: Record<string, AgentEvent[]> }> {
    return request("/api/game/transcripts");
  },
  newGame(payload: MatchSetupPayload): Promise<{ ok: boolean; state: GameSnapshot }> {
    return request("/api/game/new", { method: "POST", body: JSON.stringify(payload) });
  },
  action(payload: { action: string; amount?: number }): Promise<{ ok: boolean; state: GameSnapshot; error?: string }> {
    return request("/api/game/action", { method: "POST", body: JSON.stringify(payload) });
  },
  nextHand(): Promise<{ ok: boolean; state: GameSnapshot }> {
    return request("/api/game/next-hand", { method: "POST", body: "{}" });
  },
  setGodView(enabled: boolean): Promise<{ ok: boolean; state: GameSnapshot }> {
    return request("/api/game/god-view", { method: "POST", body: JSON.stringify({ enabled }) });
  },
  analyze(seat: number, iterations = 30000): Promise<{ ok: boolean; analysis: AnalysisResult }> {
    return request("/api/game/analyze", { method: "POST", body: JSON.stringify({ seat, iterations }) });
  },
  testLLM(payload: {
    provider_id: string;
    model_id: string;
    params: Record<string, unknown>;
  }): Promise<{ ok: boolean; text: string; latency_ms: number; usage: Record<string, number>; error?: string }> {
    return request("/api/llm/test", { method: "POST", body: JSON.stringify(payload) });
  },
};

export const iconUrl = (icon: string) => `${import.meta.env.BASE_URL}icons/${icon}`;
