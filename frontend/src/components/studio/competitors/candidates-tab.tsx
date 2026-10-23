"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, RefreshCw, Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { formatCompact } from "@/lib/format";
import {
  discoverCompetitors,
  getStudioStatus,
  listCompetitors,
  syncCompetitor,
  updateCompetitor,
  type Competitor,
  type DiscoveryResult,
} from "@/lib/studio-api";
import { COMPETITORS_KEY } from "@/components/studio/competitors/watchlist-tab";

function discoveryMessage(result: DiscoveryResult): string {
  const base = `Added ${result.added} candidate${result.added === 1 ? "" : "s"} using ${result.searches_used} SerpApi search${result.searches_used === 1 ? "" : "es"}.`;
  if (result.stopped_reason === "budget") return `${base} Stopped early: the monthly SerpApi cap was reached.`;
  if (result.stopped_reason === "youtube_quota") return `${base} Stopped early: today's YouTube quota ran out.`;
  if (result.added === 0) return `${base} Nothing new matched your size and recency filters.`;
  return base;
}

function FindMore() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["studio", "status"], queryFn: getStudioStatus });
  const discover = useMutation({
    mutationFn: discoverCompetitors,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: COMPETITORS_KEY });
      queryClient.invalidateQueries({ queryKey: ["studio", "status"] });
    },
  });
  const serp = status.data?.serpapi;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            Finds small, growing channels from your niche keywords and from the channels YouTube shows next to your
            top videos. Uses up to 8 SerpApi searches
            {serp?.configured ? ` (${serp.used} / ${serp.cap} used this month)` : ""}. Nothing is added to your
            watchlist until you approve it.
          </p>
          <Button onClick={() => discover.mutate()} disabled={discover.isPending}>
            {discover.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Search className="h-4 w-4" aria-hidden="true" />
            )}
            Find more competitors
          </Button>
        </div>
        {discover.isSuccess ? <p className="text-sm">{discoveryMessage(discover.data)}</p> : null}
        {discover.isError ? <ErrorMessage error={discover.error} /> : null}
      </CardContent>
    </Card>
  );
}

function CandidateCard({ candidate, onApproved }: { candidate: Competitor; onApproved: (c: Competitor) => void }) {
  const queryClient = useQueryClient();
  const done = () => queryClient.invalidateQueries({ queryKey: COMPETITORS_KEY });
  const approve = useMutation({
    mutationFn: () => updateCompetitor(candidate.id, { status: "watching" }),
    onSuccess: (c) => {
      done();
      onApproved(c);
    },
  });
  const reject = useMutation({
    mutationFn: () => updateCompetitor(candidate.id, { status: "rejected" }),
    onSuccess: done,
  });
  const reason = candidate.discovery_reason;

  return (
    <Card>
      <CardContent className="space-y-2 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={`https://www.youtube.com/channel/${candidate.youtube_channel_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-semibold hover:text-primary"
              >
                {candidate.title}
              </a>
              {candidate.handle ? <Badge variant="outline">{candidate.handle}</Badge> : null}
              <Badge variant="secondary">{candidate.source === "related" ? "related to your videos" : "keyword search"}</Badge>
            </div>
            <p className="text-xs text-muted-foreground tabular-nums">
              {candidate.subscriber_count === null
                ? "Subscribers hidden"
                : `≈ ${formatCompact(candidate.subscriber_count)} subscribers`}
            </p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => approve.mutate()} disabled={approve.isPending || reject.isPending}>
              <Check className="h-4 w-4" aria-hidden="true" /> Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => reject.mutate()}
              disabled={approve.isPending || reject.isPending}
            >
              <X className="h-4 w-4" aria-hidden="true" /> Reject
            </Button>
          </div>
        </div>
        {reason ? <p className="text-sm text-muted-foreground">{reason.text}</p> : null}
        {approve.isError ? <ErrorMessage error={approve.error} /> : null}
        {reject.isError ? <ErrorMessage error={reject.error} /> : null}
      </CardContent>
    </Card>
  );
}

function ApprovedNotice({ competitor, onDismiss }: { competitor: Competitor; onDismiss: () => void }) {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: () => syncCompetitor(competitor.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: COMPETITORS_KEY });
      queryClient.invalidateQueries({ queryKey: ["studio", "insights"] });
    },
  });
  return (
    <Card className="border-primary/40">
      <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
        <span>
          <strong>{competitor.title}</strong> is on your watchlist.{" "}
          {sync.isSuccess ? `${sync.data.videos_upserted} videos loaded.` : "Load its videos now to see insights?"}
        </span>
        <span className="flex gap-2">
          {sync.isSuccess ? null : (
            <Button size="sm" onClick={() => sync.mutate()} disabled={sync.isPending}>
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              Sync now
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </span>
        {sync.isError ? <ErrorMessage error={sync.error} /> : null}
      </CardContent>
    </Card>
  );
}

export function CandidatesTab() {
  const [approved, setApproved] = useState<Competitor | null>(null);
  const { data, isPending, isError, error } = useQuery({
    queryKey: [...COMPETITORS_KEY, "candidate"],
    queryFn: () => listCompetitors("candidate"),
  });

  return (
    <div className="space-y-4">
      <FindMore />
      {approved ? <ApprovedNotice competitor={approved} onDismiss={() => setApproved(null)} /> : null}
      {isPending ? (
        <div role="status" aria-label="Loading candidates" className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : isError ? (
        <ErrorMessage error={error} />
      ) : data.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No candidates waiting. Press <strong>Find more competitors</strong>. Channels you reject are never
            suggested again.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {data.map((c) => (
            <CandidateCard key={c.id} candidate={c} onApproved={setApproved} />
          ))}
        </div>
      )}
    </div>
  );
}
