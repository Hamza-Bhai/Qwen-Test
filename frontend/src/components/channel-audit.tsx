"use client";

// Phase 2 — Channel audit dashboard. Renders GET /api/v1/channels/{id}/audit
// (ChannelAuditRead). Every value shown comes from the API response; nothing
// is hardcoded or mocked. Charts use recharts (already installed).

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  CircleAlert,
  CircleCheck,
  Info,
  Loader2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { getChannelAudit } from "@/lib/api";
import {
  formatCompact,
  formatDate,
  formatDateTime,
  formatDecimal,
  formatDuration,
  formatNumber,
  formatPct,
  formatRatio,
} from "@/lib/format";
import type {
  AuditFinding,
  AuditTopicGroup,
  AuditVideoRanking,
  ChannelAudit,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

/** Map machine format identifiers to honest human wording. The backend only
 *  ever emits "estimated-*" or "unknown" — duration is not an official Shorts
 *  flag, so we never render these as verified classifications. */
export function formatFormLabel(form: string): string {
  switch (form) {
    case "estimated-shorts":
      return "Estimated Shorts";
    case "estimated-longform":
      return "Estimated long-form";
    case "unknown":
      return "Unknown format";
    default:
      // Legacy values from audits persisted before relabeling.
      if (form === "shorts") return "Estimated Shorts";
      if (form === "long") return "Estimated long-form";
      return form.replace(/-/g, " ");
  }
}

const PROVENANCE_ORDER = ["observed", "calculated", "heuristic", "unavailable", "inconclusive"];

/** Reliability-transparency panel. All text comes from the API response
 *  (`provenance`, `unavailable_metrics`, `confidence_rules`, `data_freshness`)
 *  — nothing here is hardcoded per-channel data. */
function TransparencyPanel({ audit }: { audit: ChannelAudit }) {
  const fresh = audit.data_freshness;
  const prov = audit.provenance ?? {};
  const conf = audit.confidence_rules ?? {};
  const unavailable = audit.unavailable_metrics ?? [];

  return (
    <Card aria-label="Audit data transparency">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Info className="h-4 w-4" aria-hidden="true" /> About this data &amp; its limits
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-5 text-sm md:grid-cols-2">
        {/* Data freshness */}
        <div>
          <p className="font-medium">Data freshness</p>
          {fresh ? (
            <ul className="mt-1 space-y-0.5 text-muted-foreground">
              <li>
                Videos analyzed:{" "}
                <span className="tabular-nums text-foreground">{formatNumber(fresh.sample_size_analyzed)}</span>
              </li>
              <li>
                Last video-statistics sync:{" "}
                <span className="text-foreground">
                  {fresh.last_video_stats_sync ? formatDateTime(fresh.last_video_stats_sync) : "Not recorded"}
                </span>
              </li>
              <li>
                Statistics age:{" "}
                <span className="text-foreground">
                  {fresh.stats_age_days !== null
                    ? `${formatNumber(fresh.stats_age_days)} day${fresh.stats_age_days === 1 ? "" : "s"}`
                    : "Unknown — no sync timestamp was stored"}
                </span>
              </li>
            </ul>
          ) : (
            <p className="mt-1 text-muted-foreground">The backend did not report data freshness for this audit.</p>
          )}
          {!fresh?.last_video_stats_sync && (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Freshness cannot be determined — treat statistics as potentially outdated and re-sync before relying on
              recent-performance findings.
            </p>
          )}
          {fresh?.stale_data && (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Video statistics are more than 7 days old. Re-sync the channel before acting on recency or trend
              findings.
            </p>
          )}
        </div>

        {/* Provenance taxonomy */}
        <div>
          <p className="font-medium">How to read every number in this report</p>
          <ul className="mt-1 space-y-0.5 text-muted-foreground">
            {PROVENANCE_ORDER.filter((k) => k in prov).map((k) => (
              <li key={k}>{prov[k]}</li>
            ))}
          </ul>
        </div>

        {/* Unavailable metrics */}
        <div>
          <p className="font-medium">Metrics NOT available from public data</p>
          {unavailable.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">
              {unavailable.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-muted-foreground">The backend returned no unavailable-metric list.</p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            These are never shown as zero — they are simply absent from public YouTube data.
          </p>
        </div>

        {/* Confidence rules */}
        <div>
          <p className="font-medium">What confidence labels mean</p>
          <ul className="mt-1 space-y-0.5 text-muted-foreground">
            {["high", "medium", "low", "inconclusive"]
              .filter((k) => k in conf)
              .map((k) => (
                <li key={k}>
                  <span className="capitalize font-medium text-foreground">{k}:</span> {conf[k]}
                </li>
              ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

function severityVariant(sev: string): "destructive" | "secondary" | "outline" {
  if (sev === "high") return "destructive";
  if (sev === "medium") return "secondary";
  return "outline";
}

function statusIcon(finding: AuditFinding) {
  if (finding.status === "ok") return <CircleCheck className="h-4 w-4 text-emerald-600" aria-hidden="true" />;
  if (finding.status === "inconclusive") return <CircleAlert className="h-4 w-4 text-amber-600" aria-hidden="true" />;
  if (finding.severity === "high") return <AlertTriangle className="h-4 w-4 text-red-600" aria-hidden="true" />;
  if (finding.severity === "medium") return <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />;
  return <Info className="h-4 w-4 text-muted-foreground" aria-hidden="true" />;
}

/** Compact key/value rendering of a finding's evidence dict (real data only). */
function EvidenceList({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence ?? {});
  if (entries.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 rounded-md bg-muted/50 p-3 text-xs sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-2">
          <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
          <dd className="text-right font-medium tabular-nums">
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ScoreGauge({ score, band }: { score: number; band: string }) {
  const color =
    band === "healthy" ? "#16a34a" : band === "watch" ? "#d97706" : band === "at-risk" ? "#dc2626" : "#6b7280";
  return (
    <div className="flex items-center gap-4">
      <div
        role="img"
        aria-label={`Health score ${score} out of 100`}
        className="relative flex h-24 w-24 shrink-0 items-center justify-center rounded-full border-8"
        style={{ borderColor: color }}
      >
        <span className="text-2xl font-bold tabular-nums">{score}</span>
        <span className="absolute -bottom-5 text-[10px] uppercase tracking-wide text-muted-foreground">/ 100</span>
      </div>
      <div>
        <p className="text-sm text-muted-foreground">Overall health band</p>
        <p className="text-lg font-semibold capitalize">{band}</p>
      </div>
    </div>
  );
}

function VideoRow({ video, baselineLabel }: { video: AuditVideoRanking; baselineLabel: string }) {
  const pct = video.vs_baseline_pct;
  return (
    <li className="flex items-start gap-3 py-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={video.thumbnail_url ?? ""}
        alt=""
        className="hidden h-12 w-20 shrink-0 rounded object-cover sm:block"
      />
      <div className="min-w-0 flex-1">
        <a
          href={`https://www.youtube.com/watch?v=${video.youtube_video_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="line-clamp-1 text-sm font-medium hover:underline"
        >
          {video.title}
        </a>{" "}
        {!video.mature && (
          <Badge variant="outline" className="mt-0.5 align-middle text-[10px]" title="Published under the maturity window (or undated): views/day is provisional early traction, not a settled performance rate.">
            provisional
          </Badge>
        )}
        <p className="mt-0.5 text-xs text-muted-foreground">
          {formatDate(video.published_at)} · {video.age_days}d old · {formatDuration(video.duration_seconds)} ·{" "}
          <span title="Format is estimated from video duration; YouTube's public API has no official Shorts flag.">
            {formatFormLabel(video.form)}
          </span>
        </p>
        <p className="mt-0.5 text-xs tabular-nums">
          {formatCompact(video.view_count)} views · {formatDecimal(video.views_per_day)} views/day ·{" "}
          <span className={pct !== null && pct < 0 ? "text-red-600" : "text-emerald-600"}>
            {formatPct(pct)} {baselineLabel}
          </span>
        </p>
      </div>
    </li>
  );
}

function RankingCard({
  title,
  icon,
  videos,
  emptyText,
  baselineLabel,
}: {
  title: string;
  icon: ReactNode;
  videos: AuditVideoRanking[];
  emptyText: string;
  baselineLabel: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon} {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {videos.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">{emptyText}</p>
        ) : (
          <ul className="divide-y">
            {videos.map((v) => (
              <VideoRow key={v.youtube_video_id} video={v} baselineLabel={baselineLabel} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function TopicList({ topics, kind }: { topics: AuditTopicGroup[]; kind: "strong" | "weak" }) {
  if (topics.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No qualifying {kind}-topic groups.</p>;
  }
  return (
    <ul className="space-y-3">
      {topics.map((t) => (
        <li key={t.topic} className="rounded-md border p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium capitalize">{t.topic}</p>
            <Badge variant="outline">{t.videos} videos</Badge>
          </div>
          <p className="mt-1 text-xs tabular-nums text-muted-foreground">
            median {formatDecimal(t.median_views_per_day)} views/day ·{" "}
            <span
              className={
                t.vs_channel_baseline_pct !== null && t.vs_channel_baseline_pct < 0
                  ? "text-red-600"
                  : "text-emerald-600"
              }
            >
              {formatPct(t.vs_channel_baseline_pct)} vs channel baseline
            </span>
          </p>
          {t.example_titles.length > 0 && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">e.g. “{t.example_titles[0]}”</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums" title={hint}>
        {value}
      </p>
    </div>
  );
}

export function ChannelAuditView({ channelId }: { channelId: number }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["audit", channelId, refreshKey],
    queryFn: () => getChannelAudit(channelId),
  });

  if (isPending) {
    return (
      <div role="status" aria-label="Loading channel audit" className="space-y-4 pt-10">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Running channel audit…
        </div>
        <Skeleton className="h-40" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-3 pt-10">
        <ErrorMessage error={error} />
        <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" /> Retry audit
        </Button>
      </div>
    );
  }

  const audit: ChannelAudit = data;
  const perf = audit.performance;
  const hs = audit.health_score;
  const findings = [...audit.findings].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 4) - (SEVERITY_ORDER[b.severity] ?? 4)
  );
  const detected = findings.filter((f) => f.status === "detected");
  const inconclusive = findings.filter((f) => f.status === "inconclusive");

  const distData = perf.distribution_buckets.map((b) => ({ name: b.label, videos: b.videos }));
  const monthlyData = perf.monthly_uploads.map((m) => ({ month: m.month, uploads: m.uploads }));
  const shortsVsLong = [
    { name: "Shorts", avgViews: perf.shorts_avg_views ?? 0 },
    { name: "Long-form", avgViews: perf.longform_avg_views ?? 0 },
  ];
  const formatsAvailable = perf.shorts_count + perf.longform_count > 0;

  return (
    <section aria-labelledby="audit-heading" className="space-y-6 pt-10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="audit-heading" className="text-xl font-semibold">
            Channel audit
          </h2>
          <p className="text-xs text-muted-foreground">
            Rule-based analysis of the latest {perf.videos_analyzed} of {perf.analyzed_from_total_synced} synced videos ·
            computed from stored records ({audit.computed_from}) · audited {formatDateTime(audit.audited_at)} · zero API
            quota used
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" /> Re-run audit
        </Button>
      </div>

      {audit.insufficient_data && (
        <Card className="border-amber-300 bg-amber-50 dark:bg-amber-950/30">
          <CardContent className="flex items-start gap-3 py-4 text-sm">
            <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
            <div>
              <p className="font-medium">Not enough data for a reliable audit</p>
              <ul className="mt-1 list-disc pl-5 text-muted-foreground">
                {audit.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
              <p className="mt-1 text-muted-foreground">
                Sync more videos (or wait for the scheduled sync) and re-run the audit. Nothing below is invented to fill
                gaps.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Reliability-transparency panel (freshness / provenance / limits). */}
      <TransparencyPanel audit={audit} />

      {/* A. Health score */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" aria-hidden="true" /> Health score
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {hs.score === null || !hs.band ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No component had enough data to produce a score.
              </p>
            ) : (
              <>
                <ScoreGauge score={hs.score} band={hs.band} />
                {hs.caveats.some((c) => c.startsWith("PROVISIONAL SCORE") || c.startsWith("PARTIAL COVERAGE")) && (
                  <p className="flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                    <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    This score is based on partial data only — see the coverage figure and caveats below. It is not a
                    comprehensive channel assessment.
                  </p>
                )}
                <ul className="space-y-2 pt-4">
                  {hs.components.map((c) => {
                    // Excluded / unscored components (e.g. consistency over a
                    // short observation window) are shown honestly as excluded —
                    // never rendered as a zero-width bar that implies "score 0".
                    const isCoverage = c.name === "coverage";
                    const unscored = c.excluded === true || c.score === null;
                    return (
                      <li key={c.name} className="text-sm">
                        <div className="flex items-center justify-between">
                          <span className="capitalize">
                            {c.name.replace(/_/g, " ")}
                            {isCoverage && <Badge variant="outline" className="ml-2 align-middle normal-case">score coverage</Badge>}
                          </span>
                          <span className="tabular-nums text-muted-foreground">
                            {unscored && !isCoverage
                              ? "excluded from score"
                              : `${formatNumber(c.score)}${isCoverage ? "%" : ""}${
                                  c.weight !== null && c.weight !== undefined ? ` · weight ${c.weight}` : ""
                                }`}
                          </span>
                        </div>
                        {!isCoverage && (
                          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                            {!unscored && (
                              <div
                                className="h-full rounded-full bg-primary"
                                style={{ width: `${Math.max(0, Math.min(100, c.score ?? 0))}%` }}
                              />
                            )}
                          </div>
                        )}
                        <p className="mt-0.5 text-xs text-muted-foreground">{c.note}</p>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Methodology & caveats</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="rounded-md bg-muted/50 p-2 font-mono text-xs">{hs.method}</p>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              {hs.caveats.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer font-medium text-foreground">Formula definitions</summary>
              <dl className="mt-2 space-y-1">
                {Object.entries(audit.formulas).map(([k, v]) => (
                  <div key={k}>
                    <dt className="font-mono text-foreground">{k}</dt>
                    <dd className="pl-2">{v}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </CardContent>
        </Card>
      </div>

      {/* B. Findings */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            Audit findings{" "}
            <span className="ml-1 text-sm font-normal text-muted-foreground">
              {detected.length} issue{detected.length === 1 ? "" : "s"} detected · {inconclusive.length} inconclusive
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {findings.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">The engine produced no findings.</p>
          ) : (
            <ul className="divide-y">
              {findings.map((f) => (
                <li key={f.key} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    {statusIcon(f)}
                    <p className="font-medium">{f.title}</p>
                    <Badge variant={severityVariant(f.severity)} className="capitalize">
                      {f.severity}
                    </Badge>
                    <Badge variant="outline" className="capitalize">
                      {f.status}
                    </Badge>
                    <Badge variant="outline" title={audit.confidence_rules?.[f.confidence]}>
                      confidence: {f.confidence}
                    </Badge>
                    <Badge variant="outline">{f.kind === "fact" ? "measured fact" : "heuristic"}</Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{f.explanation}</p>
                  {f.key === "recent_trend" && f.status === "inconclusive" && (
                    <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-400">
                      No verdict on recent momentum: the backend could not compare independent cohorts, so this is NOT
                      reported as “stable”, “growing” or “declining”.
                    </p>
                  )}
                  <EvidenceList evidence={f.evidence} />
                  {f.data_limitations.length > 0 && (
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      <span className="font-medium text-foreground">Data limitations:</span>{" "}
                      {f.data_limitations.join(" · ")}
                    </p>
                  )}
                  <p className="mt-1.5 text-sm">
                    <span className="font-medium">Suggested action:</span> {f.recommended_action}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* C. Performance summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Total views (analyzed)" value={formatCompact(perf.total_views)} />
        <Stat label="Avg views / video" value={formatCompact(perf.avg_views)} hint="mean of analyzed sample" />
        <Stat label="Median views / video" value={formatCompact(perf.median_views)} hint="robust central value" />
        <Stat
          label="Likes-to-views ratio"
          value={formatRatio(perf.like_to_view_ratio)}
          hint={`likes available on ${perf.likes_available_on}/${perf.videos_analyzed} videos; this is not full engagement rate`}
        />
        <Stat
          label="Comments-to-views ratio"
          value={formatRatio(perf.comment_to_view_ratio)}
          hint={`comments available on ${perf.comments_available_on}/${perf.videos_analyzed} videos`}
        />
        {(() => {
          // Cadence honesty: the backend only reports a monthly rate when the
          // observation window is long enough (its documented minimum span).
          // When cadence_reliable is false we show the raw observed count and
          // actual date span instead of extrapolating a short burst into a
          // "videos/month" publishing rate.
          const span = perf.observed_span_days;
          const spanText =
            span !== null && span !== undefined ? `${formatNumber(span)} day${span === 1 ? "" : "s"}` : null;
          if (perf.cadence_reliable && perf.uploads_per_month !== null && perf.uploads_per_month !== undefined) {
            return (
              <Stat
                label="Uploads / month"
                value={formatDecimal(perf.uploads_per_month)}
                hint={
                  (spanText ? `computed over an observed ${spanText} publication window` : "") +
                  (perf.days_since_last_upload !== null
                    ? ` · ${perf.days_since_last_upload} days since last upload`
                    : "")
                }
              />
            );
          }
          return (
            <Stat
              label="Upload activity (observed)"
              value={spanText ? `${formatNumber(perf.videos_analyzed)} in ${spanText}` : formatNumber(perf.videos_analyzed)}
              hint={
                "Observation window too short for a defensible monthly rate — this is the raw count of analyzed uploads over the actual date span, not a publishing cadence." +
                (perf.days_since_last_upload !== null ? ` ${perf.days_since_last_upload} days since last upload.` : "")
              }
            />
          );
        })()}
        <Stat label="Avg duration" value={formatDuration(perf.avg_duration_seconds)} hint="average video LENGTH, not watch time" />
        <Stat
          label="Median duration"
          value={formatDuration(perf.median_duration_seconds)}
          hint={`duration known on ${perf.duration_known_on} videos`}
        />
        <Stat label="Estimated Shorts" value={formatNumber(perf.shorts_count)} hint="duration-based estimate (≤ 5 min), not an official YouTube classification" />
        <Stat label="Estimated long-form" value={formatNumber(perf.longform_count)} hint="duration-based estimate (> 5 min), not an official YouTube classification" />
        <Stat label="Unclassified format" value={formatNumber(perf.format_unclassified)} />
        <Stat label="Hidden-stats videos" value={formatNumber(perf.hidden_stats_videos)} hint="excluded from ratio totals" />
      </div>

      {/* D. Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Performance distribution</CardTitle>
            <p className="text-xs text-muted-foreground">Videos bucketed by views/day relative to the channel median.</p>
          </CardHeader>
          <CardContent>
            {distData.length === 0 || distData.every((d) => d.videos === 0) ? (
              <p className="py-8 text-center text-sm text-muted-foreground">Not enough data for a distribution.</p>
            ) : (
              <div className="h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={distData} margin={{ top: 4, right: 8, bottom: 30, left: -18 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={50} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar dataKey="videos" fill="#3b82f6" radius={[3, 3, 0, 0]} name="videos" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Monthly uploads</CardTitle>
            <p className="text-xs text-muted-foreground">Publication trend from stored publish dates.</p>
          </CardHeader>
          <CardContent>
            {monthlyData.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">No dated uploads in the analyzed window.</p>
            ) : (
              <div className="h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={monthlyData} margin={{ top: 4, right: 8, bottom: 24, left: -18 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="month" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={46} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar dataKey="uploads" fill="#8b5cf6" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {formatsAvailable && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Estimated Shorts vs long-form</CardTitle>
              <p className="text-xs text-muted-foreground">
                Average total views per video by format ({perf.shorts_count} estimated Shorts ·{" "}
                {perf.longform_count} estimated long-form). Formats are inferred from duration only — YouTube&apos;s
                public API exposes no official Shorts flag. Totals are age-skewed; the age-normalized comparison is
                shown below the chart.
              </p>
            </CardHeader>
            <CardContent>
              <div className="h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={shortsVsLong} margin={{ top: 4, right: 8, bottom: 4, left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatCompact(v)} />
                    <Tooltip formatter={(v: number) => formatNumber(v)} />
                    <Bar dataKey="avgViews" name="avg views/video" radius={[3, 3, 0, 0]}>
                      <Cell fill="#06b6d4" />
                      <Cell fill="#f97316" />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-xs tabular-nums text-muted-foreground">
                Age-normalized median views/day: Estimated Shorts {formatDecimal(perf.shorts_median_vpd)} · Estimated
                long-form {formatDecimal(perf.longform_median_vpd)}
              </p>
              {/* Surface the backend's own conclusion status for the format
                  comparison instead of implying an actionable verdict. Small or
                  imbalanced samples, or differences under the documented 15%
                  threshold, are reported by the backend as inconclusive /
                  no-claimed-advantage — we render that verbatim from findings. */}
              {(() => {
                const fs = audit.findings.find((f) => f.key === "format_split");
                if (!fs) return null;
                if (fs.status === "detected") {
                  return (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Backend conclusion: <span className="font-medium text-foreground">{fs.title}</span> — marked
                      directional only; treat it as a prompt to A/B test, not a proven allocation decision.
                    </p>
                  );
                }
                if (fs.status === "inconclusive") {
                  return (
                    <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                      <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      Format comparison is inconclusive ({fs.explanation}) — no production-allocation change is
                      recommended.
                    </p>
                  );
                }
                return (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Backend conclusion: {fs.explanation}
                  </p>
                );
              })()}
            </CardContent>
          </Card>
        )}
      </div>

      {/* E. Rankings */}
      <div className="grid gap-4 lg:grid-cols-2">
        <RankingCard
          title="Top performers"
          icon={<TrendingUp className="h-4 w-4 text-emerald-600" aria-hidden="true" />}
          videos={audit.top_performers}
          emptyText="No ranked videos yet."
          baselineLabel="vs median views/day baseline"
        />
        <RankingCard
          title="Underperformers"
          icon={<TrendingDown className="h-4 w-4 text-red-600" aria-hidden="true" />}
          videos={audit.underperformers}
          emptyText="No videos fall clearly below the baseline."
          baselineLabel="vs median views/day baseline"
        />
      </div>

      {audit.outliers.length > 0 && (
        <RankingCard
          title="Unusual outliers"
          icon={<AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />}
          videos={audit.outliers}
          emptyText=""
          baselineLabel="vs median views/day baseline"
        />
      )}

      {/* Fresh uploads: shown separately as provisional early traction. The
          backend excludes them from mature rankings because a video only hours
          old has its age floored, exaggerating views/day — so these numbers are
          NOT presented as established long-term performance. */}
      {(audit.fresh_videos_provisional?.length ?? 0) > 0 && (
        <Card className="border-dashed">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4 text-sky-600" aria-hidden="true" /> Recent uploads — provisional early
              traction
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              These uploads are under the maturity window (or have no recorded publish date). They are excluded from
              top-performer, underperformer and outlier rankings because too little time has passed for their
              views/day to represent settled performance — compare them again once they are older.
            </p>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {audit.fresh_videos_provisional!.map((v) => (
                <VideoRow key={v.youtube_video_id} video={v} baselineLabel="vs mature-video baseline (provisional)" />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* F. Topics */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingUp className="h-4 w-4 text-emerald-600" aria-hidden="true" /> Recurring strong title keywords
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Words repeated across video titles with above-baseline median views/day — lexical grouping, not a
              semantic-topic verdict.
            </p>
          </CardHeader>
          <CardContent>
            <TopicList topics={audit.strong_topics} kind="strong" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingDown className="h-4 w-4 text-red-600" aria-hidden="true" /> Consistently weak title keywords
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Words repeated in underperforming titles; ambiguous words and synonyms are not distinguished.
            </p>
          </CardHeader>
          <CardContent>
            <TopicList topics={audit.weak_topics} kind="weak" />
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-muted-foreground">
        Note: public YouTube data contains no CTR, impressions, audience-retention or private watch-time metrics — this
        audit never claims them. Topic grouping is a keyword-cluster heuristic, not an NLP verdict.{" "}
        <Link href="/" className="text-primary underline-offset-4 hover:underline">
          Run a fresh analysis
        </Link>{" "}
        to refresh the underlying video records first.
      </p>
    </section>
  );
}
