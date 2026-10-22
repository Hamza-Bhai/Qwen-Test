// DTO types mirroring backend/app/schemas/channel.py exactly.

export interface Channel {
  id: number;
  youtube_channel_id: string;
  title: string;
  description: string | null;
  thumbnail_url: string | null;
  country: string | null;
  subscriber_count: number | null;
  video_count: number | null;
  view_count: number | null;
  channel_created_at: string | null; // ISO datetime
  last_synced_at: string | null;
}

export interface Video {
  id: number;
  youtube_video_id: string;
  title: string;
  thumbnail_url: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  view_count: number;
  like_count: number | null;
  comment_count: number | null;
  last_synced_at: string | null;
}

export interface ChannelAnalysis {
  id: number;
  channel_id: number;
  created_at: string;
  trigger: string;
  subscriber_count: number | null;
  video_count: number | null;
  view_count: number | null;
  videos_synced: number;
  quota_units_used: number;
}

// Mirrors app.analytics.metrics.ChannelSummary (asdict -> JSON).
export interface ChannelSummary {
  channel_id: number;
  youtube_channel_id: string;
  title: string;
  videos_analyzed: number;
  total_views: number;
  avg_views: number | null;
  median_views: number | null;
  avg_likes: number | null;
  like_to_view_ratio: number | null;
  avg_duration_seconds: number | null;
}

export interface ChannelOverview {
  channel: Channel;
  summary: ChannelSummary;
  recent_videos: Video[];
  history: ChannelAnalysis[];
}

export interface SyncResult {
  channel_id: number;
  videos_upserted: number;
  quota_units_used: number;
}

// ---------------------------------------------------------------------------
// Phase 2 — mirrors ChannelAuditRead in backend/app/schemas/channel.py.
// ---------------------------------------------------------------------------

export interface AuditVideoRanking {
  youtube_video_id: string;
  title: string;
  thumbnail_url: string | null;
  published_at: string | null;
  view_count: number;
  like_count: number | null;
  comment_count: number | null;
  duration_seconds: number | null;
  age_days: number;
  views_per_day: number;
  vs_baseline_pct: number | null;
  // Backend emits "estimated-shorts" | "estimated-longform" | "unknown".
  // Legacy persisted audits may still contain "shorts" | "long".
  form: string;
  /** False for uploads under the maturity window (or undated): their
   *  views/day is provisional early traction, not settled performance. */
  mature: boolean;
}

export interface AuditTopicGroup {
  topic: string;
  videos: number;
  median_views_per_day: number;
  vs_channel_baseline_pct: number | null;
  example_titles: string[];
}

export interface AuditFinding {
  key: string;
  title: string;
  severity: string; // high | medium | low | info
  status: string; // detected | ok | inconclusive | fact
  explanation: string;
  recommended_action: string;
  confidence: string; // high | medium | low | inconclusive
  evidence: Record<string, unknown>;
  data_limitations: string[];
  kind: string; // fact | heuristic
}

export interface AuditPerformance {
  videos_analyzed: number;
  analyzed_from_total_synced: number;
  total_views: number;
  avg_views: number | null;
  median_views: number | null;
  stdev_views: number | null;
  total_likes: number | null;
  total_comments: number | null;
  like_to_view_ratio: number | null;
  comment_to_view_ratio: number | null;
  likes_available_on: number;
  comments_available_on: number;
  hidden_stats_videos: number;
  uploads_per_month: number | null;
  /** Actual span of the analyzed publication window in days. */
  observed_span_days: number | null;
  /** True only when the observation window is long enough for a defensible
   *  monthly rate; false means uploads_per_month must NOT be shown as a rate. */
  cadence_reliable: boolean;
  days_since_last_upload: number | null;
  first_published_at: string | null;
  last_published_at: string | null;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  duration_known_on: number;
  shorts_count: number;
  longform_count: number;
  format_unclassified: number;
  shorts_avg_views: number | null;
  longform_avg_views: number | null;
  shorts_median_vpd: number | null;
  longform_median_vpd: number | null;
  distribution_buckets: { label: string; videos: number }[];
  monthly_uploads: { month: string; uploads: number }[];
}

export interface AuditHealthComponent {
  name: string;
  /** Null on informational entries such as the "coverage" row. */
  weight: number | null;
  /** Null when the component was excluded for lack of data. */
  score: number | null;
  note: string;
  /** Present (true) when the component lacks data and was excluded from scoring. */
  excluded?: boolean;
}

export interface AuditHealthScore {
  score: number | null;
  band: string | null; // healthy | watch | at-risk | insufficient-data
  components: AuditHealthComponent[];
  method: string;
  caveats: string[];
}

export interface ChannelAudit {
  channel_id: number;
  youtube_channel_id: string;
  title: string;
  audited_at: string;
  computed_from: string;
  config: Record<string, unknown>;
  formulas: Record<string, string>;
  performance: AuditPerformance;
  health_score: AuditHealthScore;
  findings: AuditFinding[];
  top_performers: AuditVideoRanking[];
  underperformers: AuditVideoRanking[];
  outliers: AuditVideoRanking[];
  /** Uploads too young (or undated) for mature rankings — provisional early
   *  traction only. Optional for backward compatibility with older responses. */
  fresh_videos_provisional?: AuditVideoRanking[];
  strong_topics: AuditTopicGroup[];
  weak_topics: AuditTopicGroup[];
  insufficient_data: boolean;
  notes: string[];
  // Reliability-transparency fields (mirrored from ChannelAuditRead).
  provenance?: Record<string, string>;
  unavailable_metrics?: string[];
  confidence_rules?: Record<string, string>;
  data_freshness?: AuditDataFreshness;
}

/** GET /channels/{id}/audit `data_freshness` block. Nullable timestamps are
 *  reported as null — never faked as zero-age data. */
export interface AuditDataFreshness {
  last_video_stats_sync: string | null;
  stats_age_days: number | null;
  sample_size_analyzed: number;
  stale_data: boolean;
}
