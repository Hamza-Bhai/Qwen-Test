import type { Channel, ChannelAudit, ChannelOverview, SyncResult } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** Structured API error carrying a stable code + human-readable message. */
export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface FastApiHttpDetailObject {
  code?: string;
  url?: string;
  format?: string;
  message?: string;
}

/**
 * Converts any fetch failure into an ApiError with an understandable message.
 * Handles FastAPI's three detail shapes: plain string, validation-problem
 * array (422), and our custom structured object (e.g. unsupported_url_format).
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${API_BASE_URL}. Make sure FastAPI is running (uvicorn app.main:app --port 8000) and NEXT_PUBLIC_API_BASE_URL is correct.`,
      0,
      "network_error"
    );
  }

  if (!res.ok) {
    let detail: unknown = null;
    try {
      const body = await res.json();
      detail = body?.detail ?? body;
    } catch {
      /* non-JSON error body */
    }

    if (Array.isArray(detail)) {
      // FastAPI 422 validation problems
      const first = detail[0];
      const loc = Array.isArray(first?.loc) ? first.loc.join(" > ") : "";
      throw new ApiError(
        `Invalid request${loc ? ` (${loc})` : ""}: ${first?.msg ?? "check your input"}`,
        res.status,
        "invalid_input"
      );
    }
    if (detail && typeof detail === "object") {
      const d = detail as FastApiHttpDetailObject;
      throw new ApiError(
        d.message ?? `Request failed with status ${res.status}`,
        res.status,
        d.code ?? `http_${res.status}`
      );
    }
    throw new ApiError(
      typeof detail === "string" && detail.length > 0
        ? detail
        : `Request failed with status ${res.status}`,
      res.status,
      res.status === 429 ? "quota_exceeded" : `http_${res.status}`
    );
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** POST /api/v1/channels/analyze — body field is "identifier" (ChannelCreate schema). */
export function analyzeChannel(identifier: string): Promise<ChannelOverview> {
  return request<ChannelOverview>("/channels/analyze", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
}

/** GET /api/v1/channels/{id}/overview — DB-only read, zero quota spent. */
export function getChannelOverview(id: number): Promise<ChannelOverview> {
  return request<ChannelOverview>(`/channels/${id}/overview`);
}

/** GET /api/v1/channels — stored channels list. */
export function listChannels(): Promise<Channel[]> {
  return request<Channel[]>("/channels");
}

/** POST /api/v1/channels/{id}/sync — refresh metadata + recent videos. */
export function syncChannel(id: number): Promise<SyncResult> {
  return request<SyncResult>(`/channels/${id}/sync`, { method: "POST" });
}

/** DELETE /api/v1/channels/{id} */
export function deleteChannel(id: number): Promise<void> {
  return request<void>(`/channels/${id}`, { method: "DELETE" });
}

/** GET /api/v1/channels/{id}/audit — Phase-2 rule-based audit. Read-only, zero quota. */
export function getChannelAudit(id: number): Promise<ChannelAudit> {
  return request<ChannelAudit>(`/channels/${id}/audit`);
}

/** POST /api/v1/channels/{id}/audit — explicit re-run; records one history row. */
export function runChannelAudit(id: number): Promise<ChannelAudit> {
  return request<ChannelAudit>(`/channels/${id}/audit`, { method: "POST" });
}
