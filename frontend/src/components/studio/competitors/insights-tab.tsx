"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Quote } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { formatCompact, formatDate, formatDecimal, formatNumber } from "@/lib/format";
import {
  getInsights,
  studyHook,
  type Breakout,
  type CompetitorInsight,
  type FormatComparison,
  type FormatSplit,
  type TitlePatterns,
} from "@/lib/studio-api";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const FORMAT_LABEL = { short: "Shorts", long: "Long-form" } as const;

function Pairs({ pairs, empty }: { pairs: Array<[string, number]>; empty: string }) {
  if (pairs.length === 0) return <span className="text-muted-foreground">{empty}</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {pairs.map(([k, n]) => (
        <Badge key={k} variant="secondary" className="font-normal">
          {k} <span className="ml-1 tabular-nums text-muted-foreground">×{n}</span>
        </Badge>
      ))}
    </span>
  );
}

function Patterns({ p, split }: { p: TitlePatterns | null; split: FormatSplit }) {
  if (!p) return <p className="text-sm text-muted-foreground">No videos synced yet.</p>;
  return (
    <div className="space-y-2 text-sm">
      <p className="tabular-nums">
        {p.n} titles · average {Math.round(p.avg_length)} characters · {Math.round(p.pct_with_number)}% contain a
        number · {Math.round(p.pct_question)}% ask a question
      </p>
      <p className="tabular-nums">
        Formats: {split.long} long-form · {split.short} Shorts
        {split.unclassified > 0 ? ` · ${split.unclassified} unclassified` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Recurring title words:</span>
        <Pairs pairs={p.top_keywords} empty="none yet" />
      </div>
    </div>
  );
}

function CompareRow({ fmt }: { fmt: FormatComparison }) {
  const label = FORMAT_LABEL[fmt.fmt];
  const enough = fmt.owner_median_vpd !== null && fmt.watchlist_median_vpd !== null;
  return (
    <div className="grid gap-1 rounded-md border p-3 text-sm sm:grid-cols-[8rem_1fr]">
      <p className="font-medium">{label}</p>
      {enough ? (
        <p className="tabular-nums">
          You: <strong>{formatDecimal(fmt.owner_median_vpd)}</strong> views/day · watchlist median:{" "}
          <strong>{formatDecimal(fmt.watchlist_median_vpd)}</strong> views/day (from {fmt.competitors_counted}{" "}
          channel{fmt.competitors_counted === 1 ? "" : "s"}, your {fmt.owner_videos} videos)
          {fmt.owner_vpd_per_1k_subs !== null && fmt.watchlist_vpd_per_1k_subs !== null ? (
            <>
              {" "}
              · per 1,000 subscribers: you {formatDecimal(fmt.owner_vpd_per_1k_subs)} vs{" "}
              {formatDecimal(fmt.watchlist_vpd_per_1k_subs)}
            </>
          ) : null}
        </p>
      ) : (
        <p className="text-muted-foreground">
          Not enough data: needs at least 5 videos older than 7 days per channel (you have {fmt.owner_videos};{" "}
          {fmt.competitors_counted} watched channel{fmt.competitors_counted === 1 ? "" : "s"} qualify).
        </p>
      )}
    </div>
  );
}

function HookStudy({ video }: { video: Breakout }) {
  const hook = useMutation({ mutationFn: () => studyHook(video.youtube_video_id) });
  return (
    <div className="space-y-1">
      <Button size="sm" variant="outline" onClick={() => hook.mutate()} disabled={hook.isPending}>
        {hook.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Quote className="h-4 w-4" aria-hidden="true" />
        )}
        Study hook
      </Button>
      {hook.isSuccess ? (
        <blockquote className="rounded-md border-l-2 border-primary bg-muted/40 p-2 text-sm">
          <p className="whitespace-pre-line">{hook.data.hook_text}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            First ~{hook.data.seconds_covered}s of the transcript ({hook.data.segments.length} segments). Costs one
            SerpApi search the first time; repeats are cached.
          </p>
        </blockquote>
      ) : null}
      {hook.isError ? (
        <p role="alert" className="text-xs text-destructive">
          {hook.error instanceof Error ? hook.error.message : "Could not load the transcript."}
        </p>
      ) : null}
    </div>
  );
}

function BreakoutRow({ video }: { video: Breakout }) {
  return (
    <li className="space-y-1 rounded-md border p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <a
          href={`https://www.youtube.com/watch?v=${video.youtube_video_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="line-clamp-2 text-sm font-medium hover:text-primary"
        >
          {video.title}
        </a>
        <Badge>{video.multiple.toFixed(1)}× its channel&apos;s median</Badge>
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">
        {FORMAT_LABEL[video.format]} · {formatCompact(video.view_count)} views ·{" "}
        {formatDecimal(video.views_per_day)} views/day vs {formatDecimal(video.channel_median_vpd)} normal ·{" "}
        {formatDate(video.published_at)}
      </p>
      <HookStudy video={video} />
    </li>
  );
}

function CompetitorCard({ c }: { c: CompetitorInsight }) {
  const f = c.upload_frequency;
  const times = c.breakout_publish_times;
  const weekdays = Object.entries(times.weekday_utc)
    .filter(([, n]) => n > 0)
    .map(([d, n]) => `${WEEKDAYS[Number(d)]} ×${n}`)
    .join(", ");
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {c.title}
          <span className="text-xs font-normal text-muted-foreground tabular-nums">
            {c.subscriber_count === null ? "subscribers hidden" : `≈ ${formatCompact(c.subscriber_count)} subscribers`}{" "}
            · {formatNumber(c.videos_analyzed)} videos analyzed
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Breakout videos</p>
          {c.breakout_status === "inconclusive" ? (
            <p className="text-muted-foreground">Inconclusive — {c.breakout_note}</p>
          ) : c.breakouts.length === 0 ? (
            <p className="text-muted-foreground">
              No video is running at 3× or more of this channel&apos;s normal pace (compared within each format, on
              videos older than 7 days).
            </p>
          ) : (
            <ul className="space-y-2">
              {c.breakouts.map((b) => (
                <BreakoutRow key={b.youtube_video_id} video={b} />
              ))}
            </ul>
          )}
          {times.sample > 0 ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Breakouts were published on: {weekdays} (UTC, n = {times.sample}
              {times.sample < 10 ? " — a small sample, not a rule" : ""}).
            </p>
          ) : null}
        </div>

        <p className="tabular-nums">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Cadence: </span>
          {f.reliable && f.per_30_days !== null
            ? `≈ ${formatDecimal(f.per_30_days)} uploads per 30 days (${f.uploads} uploads over ${Math.round(f.span_days ?? 0)} days)`
            : `not enough uploads to judge (${f.uploads} stored${f.span_days !== null ? ` over ${Math.round(f.span_days)} days` : ""}; needs 4+ over 4+ weeks)`}
        </p>

        <Patterns p={c.title_patterns} split={c.format_split} />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Recurring tags:</span>
          <Pairs pairs={c.top_tags} empty="none used 3+ times" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Recurring hashtags:</span>
          <Pairs pairs={c.top_hashtags} empty="none used 3+ times" />
        </div>
      </CardContent>
    </Card>
  );
}

export function InsightsTab() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["studio", "insights"], queryFn: getInsights });
  const [showAll, setShowAll] = useState(false);

  if (isPending) {
    return (
      <div role="status" aria-label="Loading insights" className="space-y-3">
        <Skeleton className="h-28" />
        <Skeleton className="h-48" />
      </div>
    );
  }
  if (isError) return <ErrorMessage error={error} />;

  if (data.competitors.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          Add and sync at least one competitor on the Watchlist tab to see insights.
        </CardContent>
      </Card>
    );
  }

  const shown = showAll ? data.competitors : data.competitors.slice(0, 5);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            You vs your watchlist{data.own_channel ? ` (${data.own_channel.title})` : ""}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Median views per day on videos older than 7 days, per format — never raw views across different-sized
            channels.
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.compare === null ? (
            <p className="text-sm text-muted-foreground">
              Choose which tracked channel is yours in the Settings tab to see this comparison.
            </p>
          ) : (
            <>
              <CompareRow fmt={data.compare.long} />
              <CompareRow fmt={data.compare.short} />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Patterns across your watchlist</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Patterns p={data.watchlist.title_patterns} split={data.watchlist.format_split} />
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Tags used 3+ times:</span>
            <Pairs pairs={data.watchlist.top_tags} empty="none" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Hashtags used 3+ times:</span>
            <Pairs pairs={data.watchlist.top_hashtags} empty="none" />
          </div>
        </CardContent>
      </Card>

      {shown.map((c) => (
        <CompetitorCard key={c.id} c={c} />
      ))}
      {data.competitors.length > 5 ? (
        <Button variant="outline" size="sm" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer channels" : `Show all ${data.competitors.length} channels`}
        </Button>
      ) : null}
    </div>
  );
}
