"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { formatCompact, formatDate } from "@/lib/format";
import { getNewUploads, markSeen } from "@/lib/studio-api";

export const NEW_UPLOADS_KEY = ["studio", "new-uploads"] as const;

export function NewUploadsTab() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery({ queryKey: NEW_UPLOADS_KEY, queryFn: getNewUploads });
  const seen = useMutation({
    mutationFn: markSeen,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NEW_UPLOADS_KEY }),
  });

  if (isPending) {
    return (
      <div role="status" aria-label="Loading new uploads" className="space-y-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  if (isError) return <ErrorMessage error={error} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Videos your watched channels published since you last marked them as seen (or in the last 7 days). An
          &quot;early signal&quot; means a young video is running far above its channel&apos;s usual pace — it is
          provisional, because young videos are front-loaded.
        </p>
        <Button variant="outline" size="sm" onClick={() => seen.mutate()} disabled={seen.isPending || data.length === 0}>
          {seen.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Check className="h-4 w-4" aria-hidden="true" />
          )}
          Mark all as seen
        </Button>
      </div>
      {seen.isError ? <ErrorMessage error={seen.error} /> : null}

      {data.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nothing new since you last looked. Sync your competitors from the Watchlist tab to check for fresh uploads.
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-2">
          {data.map((u) => (
            <li key={u.youtube_video_id}>
              <Card>
                <CardContent className="flex flex-wrap items-center justify-between gap-2 p-3">
                  <div className="min-w-0">
                    <a
                      href={`https://www.youtube.com/watch?v=${u.youtube_video_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="line-clamp-2 text-sm font-medium hover:text-primary"
                    >
                      {u.title}
                    </a>
                    <p className="text-xs text-muted-foreground tabular-nums">
                      {u.competitor_title} · {formatDate(u.published_at)} · {formatCompact(u.view_count)} views so far
                    </p>
                  </div>
                  {u.early_breakout ? (
                    <Badge variant="secondary" className="gap-1">
                      <Zap className="h-3 w-3" aria-hidden="true" />
                      Early signal — provisional
                      {u.early_multiple !== null ? ` (${u.early_multiple.toFixed(1)}× its channel's pace)` : ""}
                    </Badge>
                  ) : null}
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
