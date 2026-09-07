/**
 * Typed API client. Access tokens live in memory plus sessionStorage; the
 * client transparently rotates them once on a 401 and signs out if that fails.
 */

const BASE = "/api/v1";

export interface Tokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  role: string;
  created_at: string;
}

export interface SourceOut {
  ref: string;
  url: string;
  domain: string;
  title: string;
  snippet: string;
  credibility: number;
}

export interface ClaimOut {
  id: string;
  subquestion: string;
  text: string;
  status: "supported" | "uncertain" | "refuted";
  support: number;
  source_refs: string;
  rebuttals: string | null;
  rounds: number;
}

export interface RunSummary {
  id: string;
  query: string;
  depth: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  confidence: number;
  citation_coverage: number;
  duration_ms: number;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  error: string | null;
  tokens_used: number;
  report_markdown: string | null;
  sources: SourceOut[];
  claims: ClaimOut[];
}

export interface TraceEvent {
  seq: number;
  agent: string;
  phase: string;
  level: string;
  message: string;
  payload?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

let access: string | null = sessionStorage.getItem("ars.access");
let refresh: string | null = sessionStorage.getItem("ars.refresh");
let onSignedOut: (() => void) | null = null;

export function setSignOutHandler(handler: () => void): void {
  onSignedOut = handler;
}

export function storeTokens(tokens: Tokens): void {
  access = tokens.access_token;
  refresh = tokens.refresh_token;
  sessionStorage.setItem("ars.access", access);
  sessionStorage.setItem("ars.refresh", refresh);
}

export function clearTokens(): void {
  access = null;
  refresh = null;
  sessionStorage.removeItem("ars.access");
  sessionStorage.removeItem("ars.refresh");
}

export function hasSession(): boolean {
  return access !== null;
}

async function rotate(): Promise<boolean> {
  if (!refresh) return false;
  const response = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!response.ok) return false;
  storeTokens((await response.json()) as Tokens);
  return true;
}

async function request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (access) headers.set("Authorization", `Bearer ${access}`);

  const response = await fetch(BASE + path, { ...init, headers });

  if (response.status === 401 && !retried) {
    if (await rotate()) return request<T>(path, init, true);
    clearTokens();
    onSignedOut?.();
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { error?: string };
      message = body.error ?? message;
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(message, response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  register: (email: string, password: string) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),

  login: (email: string, password: string) =>
    request<Tokens>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  logout: async () => {
    if (refresh) {
      await request<void>("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refresh }),
      }).catch(() => undefined);
    }
    clearTokens();
  },

  me: () => request<User>("/auth/me"),

  listRuns: (limit = 12) => request<RunSummary[]>(`/research/runs?limit=${limit}`),

  getRun: (id: string) => request<RunDetail>(`/research/runs/${id}`),

  createRun: (query: string, depth: number) =>
    request<RunSummary>("/research/runs", {
      method: "POST",
      body: JSON.stringify({ query, depth }),
    }),

  cancelRun: (id: string) =>
    request<{ status: string }>(`/research/runs/${id}/cancel`, { method: "POST" }),

  deleteRun: (id: string) => request<void>(`/research/runs/${id}`, { method: "DELETE" }),

  streamTicket: (id: string) =>
    request<{ ticket: string; expires_in: number }>(`/research/runs/${id}/stream-ticket`, {
      method: "POST",
    }),
};

/** Open the SSE trace for a run. Tickets are single-run scoped and expire fast. */
export function openTrace(
  runId: string,
  ticket: string,
  onEvent: (event: TraceEvent) => void,
  onEnd: () => void,
): EventSource {
  const source = new EventSource(
    `${BASE}/research/runs/${runId}/events?ticket=${encodeURIComponent(ticket)}`,
  );
  source.onmessage = (message) => onEvent(JSON.parse(message.data) as TraceEvent);
  source.addEventListener("end", () => {
    source.close();
    onEnd();
  });
  source.onerror = () => {
    source.close();
    onEnd();
  };
  return source;
}
