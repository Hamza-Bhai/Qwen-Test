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
