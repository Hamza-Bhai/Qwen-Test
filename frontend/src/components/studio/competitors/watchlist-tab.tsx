"use client";

import { useState } from "react";
import Image from "next/image";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, Trash2, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { formatCompact, formatDateTime, formatNumber } from "@/lib/format";
import {
  addCompetitor,
  listCompetitors,
  removeCompetitor,
  syncAllCompetitors,
  syncCompetitor,
  updateCompetitor,
  type Competitor,
} from "@/lib/studio-api";

export const COMPETITORS_KEY = ["studio", "competitors"] as const;

function useRefreshAfter() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: COMPETITORS_KEY });
    queryClient.invalidateQueries({ queryKey: ["studio", "insights"] });
    queryClient.invalidateQueries({ queryKey: ["studio", "new-uploads"] });
  };
}

function AddForm() {
  const refresh = useRefreshAfter();
  const [identifier, setIdentifier] = useState("");
  const [notes, setNotes] = useState("");
  const add = useMutation({
    mutationFn: () => addCompetitor(identifier.trim(), notes.trim()),
    onSuccess: () => {
      setIdentifier("");
      setNotes("");
      refresh();
    },
  });

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <form
          className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            if (identifier.trim()) add.mutate();
          }}
        >
          <Input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="Channel URL, @handle or UC… id"
            aria-label="Competitor channel"
          />
          <Input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            aria-label="Notes"
          />
          <Button type="submit" disabled={!identifier.trim() || add.isPending}>
            {add.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plus className="h-4 w-4" aria-hidden="true" />
            )}
            Add competitor
          </Button>
        </form>
        {add.isError ? <ErrorMessage error={add.error} /> : null}
      </CardContent>
    </Card>
  );
}

function CompetitorCard({ competitor }: { competitor: Competitor }) {
  const refresh = useRefreshAfter();
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState(competitor.notes ?? "");
  const [confirmRemove, setConfirmRemove] = useState(false);

  const sync = useMutation({ mutationFn: () => syncCompetitor(competitor.id), onSuccess: refresh });
  const saveNotes = useMutation({
    mutationFn: () => updateCompetitor(competitor.id, { notes: notes.trim() || null }),
    onSuccess: () => {
      setEditing(false);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => removeCompetitor(competitor.id), onSuccess: refresh });

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          {competitor.thumbnail_url ? (
            <Image
              src={competitor.thumbnail_url}
              alt=""
              width={48}
              height={48}
              className="h-12 w-12 shrink-0 rounded-full border object-cover"
            />
          ) : (
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border bg-muted">
              <Users className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={`https://www.youtube.com/channel/${competitor.youtube_channel_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="truncate font-semibold hover:text-primary"
              >
                {competitor.title}
              </a>
              {competitor.handle ? <Badge variant="outline">{competitor.handle}</Badge> : null}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
              {competitor.subscriber_count === null
                ? "Subscribers hidden"
                : `≈ ${formatCompact(competitor.subscriber_count)} subscribers`}{" "}
              · {formatNumber(competitor.videos_stored)} videos stored ·{" "}
              {competitor.last_synced_at ? `synced ${formatDateTime(competitor.last_synced_at)}` : "never synced"}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="outline" size="sm" onClick={() => sync.mutate()} disabled={sync.isPending}>
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              Sync now
            </Button>
            {confirmRemove ? (
              <>
                <Button variant="destructive" size="sm" onClick={() => remove.mutate()} disabled={remove.isPending}>
                  Confirm remove
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove ${competitor.title}`}
                onClick={() => setConfirmRemove(true)}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </div>
        </div>

        {sync.isSuccess ? (
          <p className="text-xs text-muted-foreground">
            {sync.data.videos_upserted} videos updated · {sync.data.quota_units_used} YouTube quota units
          </p>
        ) : null}
        {sync.isError ? <ErrorMessage error={sync.error} /> : null}
        {remove.isError ? <ErrorMessage error={remove.error} /> : null}

        {editing ? (
          <div className="flex gap-2">
            <Input value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Notes" />
            <Button size="sm" onClick={() => saveNotes.mutate()} disabled={saveNotes.isPending}>
              Save
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {competitor.notes ? competitor.notes : <span className="italic">No notes.</span>}{" "}
            <button
              type="button"
              onClick={() => {
                setNotes(competitor.notes ?? "");
                setEditing(true);
              }}
              className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            >
              Edit notes
            </button>
          </p>
        )}
        {saveNotes.isError ? <ErrorMessage error={saveNotes.error} /> : null}
      </CardContent>
    </Card>
  );
}

export function WatchlistTab() {
  const refresh = useRefreshAfter();
  const { data, isPending, isError, error } = useQuery({
    queryKey: [...COMPETITORS_KEY, "watching"],
    queryFn: () => listCompetitors("watching"),
  });
  const syncAll = useMutation({ mutationFn: syncAllCompetitors, onSuccess: refresh });

  return (
    <div className="space-y-4">
      <AddForm />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Channels you watch are refreshed daily from the official YouTube API. Competitor CTR and retention are
          never shown — YouTube does not publish them.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => syncAll.mutate()}
          disabled={syncAll.isPending || !data || data.length === 0}
        >
          {syncAll.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          )}
          Sync all
        </Button>
      </div>
      {syncAll.isSuccess ? (
        <p className="text-xs text-muted-foreground">
          Synced {syncAll.data.results.filter((r) => !r.error).length} of {syncAll.data.results.length}
          {syncAll.data.results.some((r) => r.error)
            ? ` — ${syncAll.data.results.filter((r) => r.error).length} failed (see each card after retrying)`
            : ""}
        </p>
      ) : null}
      {syncAll.isError ? <ErrorMessage error={syncAll.error} /> : null}

      {isPending ? (
        <div role="status" aria-label="Loading watchlist" className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : isError ? (
        <ErrorMessage error={error} />
      ) : data.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nobody on your watchlist yet. Paste a channel above, or use the <strong>Candidates</strong> tab to find
            small, growing channels in your niche.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {data.map((c) => (
            <CompetitorCard key={c.id} competitor={c} />
          ))}
        </div>
      )}
    </div>
  );
}
