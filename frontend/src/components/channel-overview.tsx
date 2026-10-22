"use client";

import Image from "next/image";
import Link from "next/link";
import {
  CalendarDays,
  Globe,
  Loader2,
  MapPin,
  PlaySquare,
  RefreshCw,
  Users,
} from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { syncChannel } from "@/lib/api";
import {
  countryName,
  formatCompact,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatRatio,
} from "@/lib/format";
import type { ChannelOverview } from "@/lib/types";

function StatTile({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-start gap-3 p-4">
        <div className="mt-0.5 rounded-md bg-muted p-2 text-primary" aria-hidden="true">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <p className="truncate text-xl font-bold tabular-nums">{value}</p>
          {sub ? <p className="text-xs text-muted-foreground">{sub}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function ChannelOverviewView({ overview }: { overview: ChannelOverview }) {
  const queryClient = useQueryClient();
  const ch = overview.channel;
  const sync = useMutation({
    mutationFn: () => syncChannel(ch.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["overview", ch.id] });
      queryClient.invalidateQueries({ queryKey: ["channels"] });
    },
  });

  return (
    <div className="space-y-6">
      {/* Identity card */}
      <Card>
        <CardContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
          {ch.thumbnail_url ? (
            <Image
              src={ch.thumbnail_url}
              alt={`${ch.title} channel avatar`}
              width={96}
              height={96}
              className="h-24 w-24 shrink-0 rounded-full border object-cover"
            />
          ) : (
            <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full border bg-muted">
              <Users className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
            </div>
          )}
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-2xl font-bold tracking-tight">{ch.title}</h2>
              <Badge variant="secondary">{ch.youtube_channel_id.slice(0, 8)}…</Badge>
              {ch.country ? (
                <Badge variant="outline" className="gap-1">
                  <MapPin className="h-3 w-3" aria-hidden="true" />
                  {countryName(ch.country)}
                </Badge>
              ) : null}
            </div>
            <p className="whitespace-pre-line break-words text-sm text-muted-foreground">
              {ch.description && ch.description.length > 0 ? ch.description : "No public description."}
            </p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                Joined {formatDate(ch.channel_created_at)}
              </span>
              <span className="inline-flex items-center gap-1">
                <Globe className="h-3.5 w-3.5" aria-hidden="true" />
                Last synced {formatDateTime(ch.last_synced_at)}
              </span>
              <Link href={`https://www.youtube.com/channel/${ch.youtube_channel_id}`} target="_blank" rel="noopener noreferrer" className="text-primary underline-offset-4 hover:underline">
                View on YouTube ↗
              </Link>
            </div>
          </div>
          <div className="shrink-0">
            <Button
              variant="outline"
              size="sm"
              onClick={() => sync.mutate()}
              disabled={sync.isPending}
              aria-label="Refresh channel data"
            >
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {sync.isPending ? "Syncing…" : "Re-sync"}
            </Button>
            {sync.isError ? (
              <p role="alert" className="mt-2 max-w-[16rem] text-xs text-destructive">
                {sync.error instanceof Error ? sync.error.message : "Sync failed"}
              </p>
            ) : null}
            {sync.isSuccess ? (
              <p className="mt-2 text-xs text-muted-foreground">
                {sync.data.videos_upserted} videos updated · {sync.data.quota_units_used} quota units
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {/* Core stats (real API values) */}
      <section aria-labelledby="stats-heading" className="space-y-3">
        <h3 id="stats-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Channel statistics
        </h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            icon={<Users className="h-4 w-4" aria-hidden="true" />}
            label="Subscribers"
            value={ch.subscriber_count === null ? "Private" : formatNumber(ch.subscriber_count)}
            sub={ch.subscriber_count === null ? "Subscriber count hidden by owner" : undefined}
          />
          <StatTile
            icon={<PlaySquare className="h-4 w-4" aria-hidden="true" />}
            label="Total views"
            value={formatNumber(ch.view_count)}
            sub={ch.view_count !== null ? `≈ ${formatCompact(ch.view_count)}` : undefined}
          />
          <StatTile
            icon={<PlaySquare className="h-4 w-4" aria-hidden="true" />}
            label="Videos"
            value={formatNumber(ch.video_count)}
          />
          <StatTile
            icon={<CalendarDays className="h-4 w-4" aria-hidden="true" />}
            label="Created"
            value={formatDate(ch.channel_created_at)}
          />
        </div>
      </section>

      {/* Deterministic summary of synced videos */}
      <section aria-labelledby="summary-heading" className="space-y-3">
        <h3 id="summary-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Recent-video metrics ({overview.summary.videos_analyzed} analyzed)
        </h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <StatTile label="Views (recent)" value={formatNumber(overview.summary.total_views)} icon={<PlaySquare className="h-4 w-4" aria-hidden="true" />} />
          <StatTile label="Avg views" value={overview.summary.avg_views === null ? "—" : Math.round(overview.summary.avg_views).toLocaleString()} icon={<PlaySquare className="h-4 w-4" aria-hidden="true" />} />
          <StatTile label="Median views" value={overview.summary.median_views === null ? "—" : Math.round(overview.summary.median_views).toLocaleString()} icon={<PlaySquare className="h-4 w-4" aria-hidden="true" />} />
          <StatTile label="Like/view ratio" value={formatRatio(overview.summary.like_to_view_ratio)} icon={<Users className="h-4 w-4" aria-hidden="true" />} />
          <StatTile label="Avg duration" value={formatDuration(overview.summary.avg_duration_seconds)} icon={<CalendarDays className="h-4 w-4" aria-hidden="true" />} />
        </div>
      </section>

      {/* Recent videos */}
      <section aria-labelledby="videos-heading" className="space-y-3">
        <h3 id="videos-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Recent videos
        </h3>
        {overview.recent_videos.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No videos synced yet — click “Re-sync” or run the backend job.
            </CardContent>
          </Card>
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {overview.recent_videos.map((v) => (
              <li key={v.id}>
                <a
                  href={`https://www.youtube.com/watch?v=${v.youtube_video_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group block overflow-hidden rounded-xl border bg-card shadow-sm transition hover:shadow-md"
                >
                  <div className="relative aspect-video bg-muted">
                    {v.thumbnail_url ? (
                      <Image src={v.thumbnail_url} alt="" fill sizes="(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw" className="object-cover" />
                    ) : null}
                  </div>
                  <div className="space-y-1 p-3">
                    <p className="line-clamp-2 text-sm font-medium group-hover:text-primary">{v.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatCompact(v.view_count)} views · {formatDate(v.published_at)}
                      {v.duration_seconds ? ` · ${formatDuration(v.duration_seconds)}` : ""}
                    </p>
                  </div>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Analysis history */}
      <section aria-labelledby="history-heading" className="space-y-3">
        <h3 id="history-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Analysis history
        </h3>
        <Card>
          <CardContent className="p-0">
            {overview.history.length === 0 ? (
              <p className="p-6 text-center text-sm text-muted-foreground">No analysis runs recorded yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th scope="col" className="px-4 py-2 font-medium">When</th>
                      <th scope="col" className="px-4 py-2 font-medium">Trigger</th>
                      <th scope="col" className="px-4 py-2 text-right font-medium">Subscribers</th>
                      <th scope="col" className="px-4 py-2 text-right font-medium">Videos</th>
                      <th scope="col" className="px-4 py-2 text-right font-medium">Total views</th>
                      <th scope="col" className="px-4 py-2 text-right font-medium">Quota used</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.history.map((h) => (
                      <tr key={h.id} className="border-b last:border-0">
                        <td className="whitespace-nowrap px-4 py-2">{formatDateTime(h.created_at)}</td>
                        <td className="px-4 py-2"><Badge variant="secondary">{h.trigger}</Badge></td>
                        <td className="px-4 py-2 text-right tabular-nums">{formatNumber(h.subscriber_count)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{formatNumber(h.video_count)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{formatNumber(h.view_count)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{h.quota_units_used}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
