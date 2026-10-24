"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Unplug } from "lucide-react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { FindingsList } from "@/components/studio/my-channel/findings-list";
import { formatCompact, formatDate, formatDateTime, formatDecimal, formatPct } from "@/lib/format";
import {
  CONNECT_URL,
  disconnectMyChannel,
  getMyChannelOverview,
  pullMyChannel,
  type ProgressKey,
  type ProgressMetric,
} from "@/lib/studio-api";

const KPIS: { key: ProgressKey; label: string; format: (v: number | null) => string }[] = [
  { key: "views", label: "Views", format: (v) => formatCompact(v) },
  { key: "watch_minutes", label: "Watch hours", format: (v) => (v === null ? "—" : formatDecimal(v / 60)) },
  { key: "subs_net", label: "Net subscribers", format: (v) => (v === null ? "—" : v.toLocaleString("en-US")) },
  { key: "avg_view_pct", label: "Avg % viewed", format: (v) => (v === null ? "—" : `${formatDecimal(v)}%`) },
  { key: "impressions", label: "Impressions", format: (v) => formatCompact(v) },
  { key: "ctr", label: "CTR", format: (v) => (v === null ? "—" : `${formatDecimal(v, 2)}%`) },
];

function Change({ value, label }: { value: number | null; label: string }) {
  const tone = value === null ? "text-muted-foreground" : value >= 0 ? "text-primary" : "text-destructive";
  return (
    <span className={`tabular-nums ${tone}`}>
      {formatPct(value)} <span className="text-muted-foreground">{label}</span>
    </span>
  );
}

function KpiCard({ label, metric, format }: { label: string; metric: ProgressMetric; format: (v: number | null) => string }) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <p className="text-xs text-muted-foreground">{label} · last 28 days</p>
        <p className="text-xl font-semibold tabular-nums">{format(metric.last_28)}</p>
        <p className="flex flex-col text-xs">
          <Change value={metric.change_28_pct} label="vs previous 28 days" />
          <Change value={metric.change_7_pct} label="last 7 vs previous 7" />
        </p>
      </CardContent>
    </Card>
  );
}

export function ChannelOverview({ channelId }: { channelId: number }) {
  const queryClient = useQueryClient();
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const key = ["studio", "my-channel", channelId] as const;
  const { data, isPending, isError, error } = useQuery({ queryKey: key, queryFn: () => getMyChannelOverview(channelId) });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["studio", "my-channels"] });
  };
  const pull = useMutation({ mutationFn: () => pullMyChannel(channelId), onSuccess: refresh });
  const disconnect = useMutation({
    mutationFn: () => disconnectMyChannel(channelId),
    onSuccess: () => {
      setConfirmDisconnect(false);
      refresh();
    },
  });

  if (isPending) {
    return (
      <div role="status" aria-label="Loading channel analytics" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (isError) return <ErrorMessage error={error} />;

  const { channel } = data;
  const connected = channel.status === "connected";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Last pulled: {channel.last_pulled_at ? formatDateTime(channel.last_pulled_at) : "not yet"} · pulled again every day
          while the app runs.
        </p>
        <div className="flex flex-wrap gap-2">
          {connected ? (
            <Button variant="outline" size="sm" onClick={() => pull.mutate()} disabled={pull.isPending}>
              {pull.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              Pull now
            </Button>
          ) : (
            <a href={CONNECT_URL} className={buttonVariants({ size: "sm" })}>
              Connect again
            </a>
          )}
          {connected && !confirmDisconnect ? (
            <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(true)}>
              <Unplug className="h-4 w-4" aria-hidden="true" />
              Disconnect
            </Button>
          ) : null}
          {confirmDisconnect ? (
            <span className="inline-flex items-center gap-2 text-sm">
              Stop pulling this channel? History is kept.
              <Button variant="destructive" size="sm" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
                Yes, disconnect
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </Button>
            </span>
          ) : null}
        </div>
      </div>
      {channel.status === "needs_reconnect" || channel.last_error ? (
        <Alert variant={channel.status === "needs_reconnect" ? "destructive" : "default"}>
          <AlertDescription>{channel.last_error ?? "Google access ended — connect this channel again."}</AlertDescription>
        </Alert>
      ) : null}
      {pull.isError ? <ErrorMessage error={pull.error} /> : null}
      {disconnect.isError ? <ErrorMessage error={disconnect.error} /> : null}
      {pull.data?.reach_error ? (
        <Alert>
          <AlertDescription>Impressions/CTR could not be read this time: {pull.data.reach_error}</AlertDescription>
        </Alert>
      ) : null}
      {data.reach_status !== "ok" ? (
        <p className="text-xs text-muted-foreground">
          {data.reach_status === "waiting_for_first_report"
            ? "Impressions and CTR appear 1–2 days after connecting: Google prepares those reports once a day."
            : "Impressions and CTR start after the first pull."}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {KPIS.map((k) => (
          <KpiCard key={k.key} label={k.label} metric={data.progress[k.key]} format={k.format} />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Daily views (last 90 days)</CardTitle>
        </CardHeader>
        <CardContent className="h-64">
          {data.daily.length === 0 ? (
            <p className="text-sm text-muted-foreground">No daily data yet — press Pull now.</p>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.daily}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tick={{ fontSize: 10 }} tickFormatter={(d: string) => d.slice(5)} minTickGap={20} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatCompact(v)} />
                <Tooltip />
                <Line type="monotone" dataKey="views" stroke="hsl(var(--primary))" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Diagnosis</h2>
        <FindingsList findings={data.findings} />
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Traffic sources (last 28 days)</CardTitle>
          </CardHeader>
          <CardContent>
            {data.traffic.length === 0 ? (
              <p className="text-sm text-muted-foreground">No traffic data yet.</p>
            ) : (
              <table className="w-full text-sm">
                <tbody>
                  {data.traffic.map((t) => (
                    <tr key={t.source} className="border-b last:border-0">
                      <td className="py-1.5">{t.source.replaceAll("_", " ").toLowerCase()}</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCompact(t.views)}</td>
                      <td className="py-1.5 text-right tabular-nums text-muted-foreground">{formatDecimal(t.share)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Videos</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            {data.videos.length === 0 ? (
              <p className="text-sm text-muted-foreground">No videos pulled yet.</p>
            ) : (
              <table className="w-full min-w-[560px] text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-1.5 font-medium">Video</th>
                    <th className="py-1.5 text-right font-medium">Views</th>
                    <th className="py-1.5 text-right font-medium">Avg % viewed</th>
                    <th className="py-1.5 text-right font-medium">CTR 28d</th>
                    <th className="py-1.5 text-right font-medium">Kept at 30%</th>
                  </tr>
                </thead>
                <tbody>
                  {data.videos.map((v) => (
                    <tr key={v.youtube_video_id} className="border-b last:border-0">
                      <td className="max-w-xs py-1.5">
                        <a
                          href={`https://www.youtube.com/watch?v=${v.youtube_video_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="line-clamp-1 hover:text-primary"
                        >
                          {v.title}
                        </a>
                        <span className="text-xs text-muted-foreground">
                          {formatDate(v.published_at)}
                          {v.is_short ? (
                            <Badge variant="outline" className="ml-2">
                              Short
                            </Badge>
                          ) : null}
                        </span>
                      </td>
                      <td className="py-1.5 text-right tabular-nums">{formatCompact(v.views)}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {v.avg_view_pct === null ? "—" : `${formatDecimal(v.avg_view_pct)}%`}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {v.ctr_28d === null ? "—" : `${formatDecimal(v.ctr_28d, 2)}%`}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {v.retention_30 === null ? "—" : `${Math.round(v.retention_30 * 100)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
