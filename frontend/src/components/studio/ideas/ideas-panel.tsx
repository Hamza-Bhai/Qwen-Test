"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lightbulb, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { ChannelSwitcher, NoChannelYet, useChannelChoice } from "@/components/studio/channel-switcher";
import { IdeaCard } from "@/components/studio/ideas/idea-card";
import { formatDate } from "@/lib/format";
import {
  generateIdeas,
  getAdvisor,
  getMyChannelOverview,
  getWhatWorked,
  listIdeas,
  markIdeaPublished,
  updateIdea,
  type Idea,
  type IdeaStatus,
} from "@/lib/studio-api";
import { cn } from "@/lib/utils";

const FILTERS: { id: string; label: string; match: (i: Idea) => boolean }[] = [
  { id: "open", label: "To decide", match: (i) => i.status === "new" || i.status === "saved" },
  { id: "planned", label: "Approved & scheduled", match: (i) => i.status === "approved" || i.status === "scheduled" },
  { id: "published", label: "Published", match: (i) => i.status === "published" },
  { id: "rejected", label: "Rejected", match: (i) => i.status === "rejected" },
];

function nextSevenDays(): string[] {
  const days: string[] = [];
  const today = new Date();
  for (let i = 0; i < 7; i++) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
    days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
  }
  return days;
}

function PlannerWeek({ ideas }: { ideas: Idea[] }) {
  const days = nextSevenDays();
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Next 7 days</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {days.map((day) => {
          const planned = ideas.filter((i) => i.status === "scheduled" && i.planned_date === day);
          return (
            <div key={day} className="min-h-16 rounded-md border p-2 text-xs">
              <p className="font-medium">{new Date(`${day}T00:00:00`).toLocaleDateString("en-US", { weekday: "short", day: "numeric" })}</p>
              {planned.length === 0 ? (
                <p className="text-muted-foreground">—</p>
              ) : (
                planned.map((i) => (
                  <p key={i.id} className="line-clamp-2">
                    {i.format === "short" ? "▮ " : "▬ "}
                    {i.topic}
                  </p>
                ))
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function WhatWorkedCard({ channelId }: { channelId: number }) {
  const { data } = useQuery({ queryKey: ["studio", "what-worked", channelId], queryFn: () => getWhatWorked(channelId) });
  if (!data) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">What has worked for me</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {data.groups.length === 0 ? (
          <p className="text-muted-foreground">
            Not enough results yet. Each published idea is checked at day 7 and day 28; a group needs 3 results.
          </p>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-1 font-medium">Group</th>
                <th className="py-1 text-right font-medium">Worked</th>
                <th className="py-1 text-right font-medium">Neutral</th>
                <th className="py-1 text-right font-medium">Didn&apos;t</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={g.group} className="border-b last:border-0">
                  <td className="py-1">{g.group.replace(":", ": ")}</td>
                  <td className="py-1 text-right tabular-nums">{g.worked}</td>
                  <td className="py-1 text-right tabular-nums">{g.neutral}</td>
                  <td className="py-1 text-right tabular-nums">{g.didnt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-xs text-muted-foreground">{data.caveat}</p>
      </CardContent>
    </Card>
  );
}

function IdeasView({ channelId }: { channelId: number }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("open");
  const key = ["studio", "ideas", channelId] as const;
  const ideas = useQuery({ queryKey: key, queryFn: () => listIdeas(channelId) });
  const advisor = useQuery({ queryKey: ["studio", "advisor", channelId], queryFn: () => getAdvisor(channelId) });
  const overview = useQuery({ queryKey: ["studio", "my-channel", channelId], queryFn: () => getMyChannelOverview(channelId) });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["studio", "advisor", channelId] });
    queryClient.invalidateQueries({ queryKey: ["studio", "what-worked", channelId] });
  };
  const generate = useMutation({ mutationFn: () => generateIdeas(channelId), onSuccess: refresh });
  const change = useMutation({
    mutationFn: ({ id, status, day, video }: { id: number; status?: IdeaStatus; day?: string; video?: string }) =>
      video ? markIdeaPublished(id, video) : updateIdea(id, day ? { planned_date: day } : { status }),
    onSuccess: refresh,
  });

  if (ideas.isPending) {
    return (
      <div role="status" aria-label="Loading ideas" className="space-y-3">
        <Skeleton className="h-20" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (ideas.isError) return <ErrorMessage error={ideas.error} />;

  const ai = advisor.data?.ai;
  const shown = ideas.data.filter(FILTERS.find((f) => f.id === filter)!.match);
  const videos = (overview.data?.videos ?? []).slice(0, 20);

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
          <p className="text-muted-foreground">
            {ai && !ai.configured
              ? "Set AI_PROVIDER=claude_cli (or AI_API_KEY) in backend/.env and restart the backend to get daily video ideas."
              : `New ideas arrive once a day while the app runs; ideas that repeat your own titles are skipped.${ai ? ` AI calls today: ${ai.used_today}/${ai.cap}.` : ""}`}
          </p>
          <Button size="sm" onClick={() => generate.mutate()} disabled={generate.isPending || !ai?.configured || ai.used_today >= ai.cap}>
            {generate.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Lightbulb className="h-4 w-4" aria-hidden="true" />}
            Generate ideas now
          </Button>
        </CardContent>
      </Card>
      {generate.isError ? <ErrorMessage error={generate.error} /> : null}
      {change.isError ? <ErrorMessage error={change.error} /> : null}

      <PlannerWeek ideas={ideas.data} />

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Idea status">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            onClick={() => setFilter(f.id)}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              filter === f.id ? "border-primary bg-primary/10 font-medium" : "hover:bg-muted"
            )}
          >
            {f.label} ({ideas.data.filter(f.match).length})
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing here yet.</p>
      ) : (
        <ul className="space-y-3">
          {shown.map((idea) => (
            <li key={idea.id}>
              <IdeaCard
                idea={idea}
                videos={videos}
                actions={{
                  busy: change.isPending,
                  setStatus: (status) => change.mutate({ id: idea.id, status }),
                  schedule: (day) => change.mutate({ id: idea.id, day }),
                  published: (video) => change.mutate({ id: idea.id, video }),
                }}
              />
              <p className="mt-1 text-xs text-muted-foreground">Suggested {formatDate(idea.created_at)}</p>
            </li>
          ))}
        </ul>
      )}

      <WhatWorkedCard channelId={channelId} />
    </div>
  );
}

export function IdeasPanel() {
  const { data, isPending, isError, error, activeId, setSelected } = useChannelChoice();
  if (isPending) return <Skeleton className="h-12" />;
  if (isError) return <ErrorMessage error={error} />;
  if (data.length === 0 || activeId === null) return <NoChannelYet what="the ideas" />;
  return (
    <div className="space-y-4">
      {data.length > 1 ? <ChannelSwitcher channels={data} activeId={activeId} onSelect={setSelected} /> : null}
      <IdeasView key={activeId} channelId={activeId} />
    </div>
  );
}
