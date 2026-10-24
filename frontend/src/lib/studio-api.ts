import { API_BASE_URL, ApiError } from "./api";

export interface SerpApiStatus {
  configured: boolean;
  month: string;
  used: number;
  cap: number;
  remaining: number;
}

export interface OwnChannelBrief {
  id: number;
  title: string;
}

export interface StudioStatus {
  serpapi: SerpApiStatus;
  keywords: string[];
  own_channel: OwnChannelBrief | null;
  auto_discovery: boolean;
}

export interface OwnChannelInfo {
  channel: (OwnChannelBrief & { subscriber_count: number | null }) | null;
  candidates: OwnChannelBrief[];
}

export type CompetitorStatus = "candidate" | "watching" | "rejected";

export interface DiscoveryReason {
  seeds: string[];
  appearances: number;
  recent_median_views: number;
  views_per_subscriber: number | null;
  days_since_last_upload: number;
  text: string;
}

export interface Competitor {
  id: number;
  youtube_channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  country: string | null;
  subscriber_count: number | null; // null = hidden by the channel
  video_count: number | null;
  status: CompetitorStatus;
  source: string;
  discovery_reason: DiscoveryReason | null;
  notes: string | null;
  added_at: string;
  last_synced_at: string | null;
  videos_stored: number;
}

export interface SyncResult {
  competitor: string;
  videos_upserted: number;
  quota_units_used: number;
}

export interface SyncAllResult {
  results: Array<{ competitor: string; videos_upserted?: number; quota_units_used?: number; error?: string }>;
}

export interface Breakout {
  youtube_video_id: string;
  title: string;
  format: "short" | "long";
  view_count: number;
  views_per_day: number;
  channel_median_vpd: number;
  multiple: number;
  published_at: string | null;
}

export interface UploadFrequency {
  uploads: number;
  span_days: number | null;
  per_30_days: number | null;
  reliable: boolean;
}

export interface TitlePatterns {
  n: number;
  avg_length: number;
  pct_with_number: number;
  pct_question: number;
  top_keywords: Array<[string, number]>;
}

export interface FormatSplit {
  short: number;
  long: number;
  unclassified: number;
}

export interface PublishTimes {
  sample: number;
  weekday_utc: Record<string, number>;
  hour_utc: Record<string, number>;
}

export interface CompetitorInsight {
  id: number;
  title: string;
  subscriber_count: number | null;
  videos_analyzed: number;
  breakouts: Breakout[];
  breakout_status: "ok" | "inconclusive";
  breakout_note: string | null;
  upload_frequency: UploadFrequency;
  format_split: FormatSplit;
  title_patterns: TitlePatterns | null;
  top_tags: Array<[string, number]>;
  top_hashtags: Array<[string, number]>;
  breakout_publish_times: PublishTimes;
}

export interface FormatComparison {
  fmt: "short" | "long";
  owner_median_vpd: number | null;
  owner_videos: number;
  watchlist_median_vpd: number | null;
  competitors_counted: number;
  owner_vpd_per_1k_subs: number | null;
  watchlist_vpd_per_1k_subs: number | null;
}

export interface Insights {
  competitors: CompetitorInsight[];
  watchlist: {
    top_tags: Array<[string, number]>;
    top_hashtags: Array<[string, number]>;
    title_patterns: TitlePatterns | null;
    format_split: FormatSplit;
  };
  compare: { short: FormatComparison; long: FormatComparison } | null;
  own_channel: OwnChannelBrief | null;
}

export interface NewUpload {
  competitor_id: number;
  competitor_title: string;
  youtube_video_id: string;
  title: string;
  published_at: string;
  view_count: number;
  early_breakout: boolean;
  early_multiple: number | null;
}

export interface HookResult {
  youtube_video_id: string;
  hook_text: string;
  segments: Array<{ start_ms: number; text: string }>;
  seconds_covered: number;
}

export interface DiscoveryResult {
  added: number;
  considered: number;
  searches_used: number;
  stopped_reason: "budget" | "youtube_quota" | "serpapi_error" | null;
  message: string | null;
  competitor_ids: number[];
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

// ---- settings
export function getStudioStatus(): Promise<StudioStatus> {
  return studioRequest<StudioStatus>("/status");
}

export async function saveKeywords(keywords: string[]): Promise<string[]> {
  const body = await studioRequest<{ keywords: string[] }>("/keywords", json("PUT", { keywords }));
  return body.keywords;
}

export function getOwnChannel(): Promise<OwnChannelInfo> {
  return studioRequest<OwnChannelInfo>("/own-channel");
}

export function setOwnChannel(channelId: number): Promise<OwnChannelInfo> {
  return studioRequest<OwnChannelInfo>("/own-channel", json("PUT", { channel_id: channelId }));
}

export async function setAutoDiscovery(enabled: boolean): Promise<boolean> {
  const body = await studioRequest<{ enabled: boolean }>("/auto-discovery", json("PUT", { enabled }));
  return body.enabled;
}

// ---- competitors
export function listCompetitors(status?: CompetitorStatus): Promise<Competitor[]> {
  return studioRequest<Competitor[]>(`/competitors${status ? `?status=${status}` : ""}`);
}

export function addCompetitor(identifier: string, notes?: string): Promise<Competitor> {
  return studioRequest<Competitor>("/competitors", json("POST", { identifier, notes: notes || null }));
}

export function updateCompetitor(
  id: number,
  patch: { status?: CompetitorStatus; notes?: string | null }
): Promise<Competitor> {
  return studioRequest<Competitor>(`/competitors/${id}`, json("PATCH", patch));
}

export function removeCompetitor(id: number): Promise<void> {
  return studioRequest<void>(`/competitors/${id}`, json("DELETE"));
}

export function syncCompetitor(id: number): Promise<SyncResult> {
  return studioRequest<SyncResult>(`/competitors/${id}/sync`, json("POST"));
}

export function syncAllCompetitors(): Promise<SyncAllResult> {
  return studioRequest<SyncAllResult>("/competitors/sync", json("POST"));
}

export function discoverCompetitors(): Promise<DiscoveryResult> {
  return studioRequest<DiscoveryResult>("/competitors/discover", json("POST"));
}

export function getInsights(): Promise<Insights> {
  return studioRequest<Insights>("/competitors/insights");
}

export function getNewUploads(): Promise<NewUpload[]> {
  return studioRequest<NewUpload[]>("/competitors/new-uploads");
}

export function markSeen(): Promise<{ marked: number }> {
  return studioRequest<{ marked: number }>("/competitors/mark-seen", json("POST"));
}

export function studyHook(youtubeVideoId: string): Promise<HookResult> {
  return studioRequest<HookResult>(`/competitors/videos/${youtubeVideoId}/hook`, json("POST"));
}

export function getHook(youtubeVideoId: string): Promise<HookResult> {
  return studioRequest<HookResult>(`/competitors/videos/${youtubeVideoId}/hook`);
}

// ---- my channels (private analytics)
export type ConnectionStatus = "connected" | "disconnected" | "needs_reconnect";

export interface MyChannel {
  id: number;
  youtube_channel_id: string;
  title: string;
  thumbnail_url: string | null;
  status: ConnectionStatus;
  last_error: string | null;
  connected_at: string | null;
  last_pulled_at: string | null;
}

export interface ProgressMetric {
  last_7: number | null;
  prev_7: number | null;
  change_7_pct: number | null;
  last_28: number | null;
  prev_28: number | null;
  change_28_pct: number | null;
}

export type ProgressKey = "views" | "watch_minutes" | "subs_net" | "avg_view_pct" | "impressions" | "ctr";

export interface Finding {
  code: string;
  title: string;
  status: "issue" | "good" | "info" | "inconclusive";
  confidence: "high" | "medium" | "low";
  summary: string;
  evidence: Record<string, unknown>;
  action: string | null;
}

export interface MyVideo {
  youtube_video_id: string;
  title: string;
  published_at: string | null;
  is_short: boolean | null;
  views: number | null;
  avg_view_pct: number | null;
  impressions_28d: number | null;
  ctr_28d: number | null;
  retention_30: number | null;
}

export interface MyChannelOverview {
  channel: MyChannel;
  progress: Record<ProgressKey, ProgressMetric>;
  daily: { day: string; views: number; watch_minutes: number; subs_net: number; avg_view_pct: number }[];
  traffic: { source: string; views: number; share: number }[];
  videos: MyVideo[];
  findings: Finding[];
  public_channel_id: number | null;
  reach_status: "ok" | "waiting_for_first_report" | "no_job";
}

export interface PullResult {
  connection_id: number;
  videos: number;
  channel_days: number;
  traffic_rows: number;
  video_totals: number;
  retention_videos: number;
  reach?: { files: number; rows: number };
  reach_error?: string;
}

/** Full-page navigation target: the backend redirects to Google's consent screen. */
export const CONNECT_URL = `${API_BASE_URL}/studio/oauth/start`;

export function listMyChannels(): Promise<MyChannel[]> {
  return studioRequest<MyChannel[]>("/my-channels");
}

export function getMyChannelOverview(id: number): Promise<MyChannelOverview> {
  return studioRequest<MyChannelOverview>(`/my-channels/${id}/overview`);
}

export function pullMyChannel(id: number): Promise<PullResult> {
  return studioRequest<PullResult>(`/my-channels/${id}/pull`, json("POST"));
}

export function disconnectMyChannel(id: number): Promise<void> {
  return studioRequest<void>(`/my-channels/${id}`, json("DELETE"));
}

// ---- advisor (Dashboard) and ideas (Ideas & Plan)
export type Confidence = "high" | "medium" | "low";

export interface EvidenceRef {
  id: string;
  kind: string;
  text: string;
}

export interface AdviceAnswer {
  question: string;
  answer: string;
  evidence_ids: string[];
  confidence: Confidence;
}

export interface Advice {
  answers: AdviceAnswer[];
  evidence: EvidenceRef[];
  created_at: string;
  model: string;
}

export interface AiUsage {
  configured: boolean;
  used_today: number;
  cap: number;
}

export interface AdvisorView {
  ai: AiUsage;
  questions: Record<string, string>;
  advice: Advice | null;
  findings: Finding[];
}

export type IdeaStatus = "new" | "saved" | "approved" | "rejected" | "scheduled" | "published";

export interface IdeaPayload {
  topic: string;
  reason: string;
  format: "short" | "long";
  titles: string[];
  hook: string;
  outline: string[];
  duration_minutes: number;
  description: string;
  hashtags: string[];
  tags: string[];
  competitor_refs: string[];
  evidence_ids: string[];
  confidence: Confidence;
  priority: number;
  upload_time: string;
}

export interface IdeaOutcome {
  checkpoint: number;
  verdict: "worked" | "neutral" | "didnt" | "no_baseline";
  ratio: number | null;
  views_per_day: number;
  baseline_vpd: number | null;
}

export interface Idea {
  id: number;
  status: IdeaStatus;
  format: "short" | "long";
  topic: string;
  payload: IdeaPayload;
  created_at: string | null;
  planned_date: string | null;
  published_video_id: string | null;
  published_at: string | null;
  baseline: { format: string; median_vpd: number | null; videos: number } | null;
  outcomes: IdeaOutcome[];
}

export interface WhatWorked {
  groups: { group: string; worked: number; neutral: number; didnt: number; n: number }[];
  too_few: string[];
  caveat: string;
}

export function getAdvisor(channelId: number): Promise<AdvisorView> {
  return studioRequest<AdvisorView>(`/advisor/${channelId}`);
}

export function generateAdvice(channelId: number, force = false): Promise<Advice> {
  return studioRequest<Advice>(`/advisor/${channelId}/generate?force=${force}`, json("POST"));
}

export function listIdeas(channelId: number): Promise<Idea[]> {
  return studioRequest<Idea[]>(`/ideas/${channelId}`);
}

export function generateIdeas(channelId: number, force = false): Promise<Idea[]> {
  return studioRequest<Idea[]>(`/ideas/${channelId}/generate?force=${force}`, json("POST"));
}

export function updateIdea(ideaId: number, patch: { status?: IdeaStatus; planned_date?: string }): Promise<Idea> {
  return studioRequest<Idea>(`/ideas/item/${ideaId}`, json("PATCH", patch));
}

export function markIdeaPublished(ideaId: number, youtubeVideoId: string): Promise<Idea> {
  return studioRequest<Idea>(`/ideas/item/${ideaId}/published`, json("POST", { youtube_video_id: youtubeVideoId }));
}

export function getWhatWorked(channelId: number): Promise<WhatWorked> {
  return studioRequest<WhatWorked>(`/ideas/${channelId}/what-worked`);
}
