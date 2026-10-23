import { API_BASE_URL, ApiError } from "./api";

export interface SerpApiStatus {
  configured: boolean;
  month: string;
  used: number;
  cap: number;
  remaining: number;
}

export interface StudioStatus {
  serpapi: SerpApiStatus;
  keywords: string[];
}

/** Growth Studio calls. Kept apart from api.ts so the public-channel client stays untouched. */
async function studioRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/studio${path}`, {
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
      detail = (await res.json())?.detail;
    } catch {
      /* non-JSON error body */
    }
    const message =
      typeof detail === "string" && detail.length > 0
        ? detail
        : `Request failed with status ${res.status}`;
    throw new ApiError(message, res.status, `http_${res.status}`);
  }
  return (await res.json()) as T;
}

export function getStudioStatus(): Promise<StudioStatus> {
  return studioRequest<StudioStatus>("/status");
}

export async function saveKeywords(keywords: string[]): Promise<string[]> {
  const body = await studioRequest<{ keywords: string[] }>("/keywords", {
    method: "PUT",
    body: JSON.stringify({ keywords }),
  });
  return body.keywords;
}
