"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { formatDate, formatDecimal } from "@/lib/format";
import type { Idea, IdeaOutcome, IdeaStatus, MyVideo } from "@/lib/studio-api";

const VERDICT: Record<IdeaOutcome["verdict"], { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  worked: { label: "Worked", variant: "default" },
  neutral: { label: "Neutral", variant: "secondary" },
  didnt: { label: "Didn't work", variant: "destructive" },
  no_baseline: { label: "No baseline", variant: "outline" },
};

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={`Copy ${label}`}
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
    </Button>
  );
}

export interface IdeaActions {
  setStatus: (status: IdeaStatus) => void;
  schedule: (day: string) => void;
  published: (videoId: string) => void;
  busy: boolean;
}

export function IdeaCard({ idea, videos, actions }: { idea: Idea; videos: MyVideo[]; actions: IdeaActions }) {
  const p = idea.payload;
  const [day, setDay] = useState(idea.planned_date ?? "");
  const [videoId, setVideoId] = useState("");
  const open = idea.status !== "rejected" && idea.status !== "published";

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="font-semibold">{idea.topic}</p>
            <p className="text-sm text-muted-foreground">{p.reason}</p>
          </div>
          <div className="flex flex-wrap gap-1">
            <Badge variant="outline">{idea.format === "short" ? "Short" : "Long-form"} · {formatDecimal(p.duration_minutes)} min</Badge>
            <Badge variant="secondary">priority {p.priority}</Badge>
            <Badge variant="outline">confidence {p.confidence}</Badge>
            <Badge>{idea.status}</Badge>
          </div>
        </div>

        <div>
          <p className="text-xs font-medium text-muted-foreground">Title options</p>
          <ul className="space-y-0.5 text-sm">
            {p.titles.map((t) => (
              <li key={t} className="flex items-center justify-between gap-2">
                <span>{t}</span>
                <CopyButton text={t} label="title" />
              </li>
            ))}
          </ul>
        </div>

        <p className="text-sm">
          <span className="font-medium">Hook: </span>
          {p.hook}
        </p>

        <details className="text-sm">
          <summary className="cursor-pointer select-none text-muted-foreground">Outline, description, tags</summary>
          <ol className="mt-2 list-decimal space-y-0.5 pl-5">
            {p.outline.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <div className="mt-2 flex items-start justify-between gap-2">
            <p className="whitespace-pre-line text-muted-foreground">{p.description}</p>
            <CopyButton text={p.description} label="description" />
          </div>
          {p.hashtags.length || p.tags.length ? (
            <div className="mt-2 flex items-start justify-between gap-2">
              <p className="text-xs text-muted-foreground">{[...p.hashtags, ...p.tags].join(", ")}</p>
              <CopyButton text={[...p.hashtags, ...p.tags].join(", ")} label="hashtags and tags" />
            </div>
          ) : null}
        </details>

        <p className="text-xs text-muted-foreground">
          Upload time: {p.upload_time}
          {p.competitor_refs.length ? (
            <>
              {" · "}Competitor examples:{" "}
              {p.competitor_refs.map((ref, i) => (
                <a
                  key={ref}
                  href={`https://www.youtube.com/watch?v=${ref.slice(2)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-primary"
                >
                  {i + 1}
                </a>
              )).reduce<React.ReactNode[]>((acc, el, i) => (i ? [...acc, ", ", el] : [el]), [])}
            </>
          ) : null}
        </p>

        {idea.status === "published" ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <a
              href={`https://www.youtube.com/watch?v=${idea.published_video_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-primary"
            >
              Published {formatDate(idea.published_at)}
            </a>
            {idea.outcomes.length === 0 ? <Badge variant="outline">Result after day 7</Badge> : null}
            {idea.outcomes.map((o) => (
              <Badge key={o.checkpoint} variant={VERDICT[o.verdict].variant}>
                Day {o.checkpoint}: {VERDICT[o.verdict].label}
                {o.ratio !== null ? ` (${formatDecimal(o.ratio, 2)}× your usual)` : ""}
              </Badge>
            ))}
            {idea.outcomes.length ? <span className="text-muted-foreground">Single video — noisy.</span> : null}
          </div>
        ) : null}

        {open ? (
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            {idea.status === "new" ? (
              <Button variant="outline" size="sm" disabled={actions.busy} onClick={() => actions.setStatus("saved")}>
                Save
              </Button>
            ) : null}
            {idea.status === "new" || idea.status === "saved" ? (
              <Button size="sm" disabled={actions.busy} onClick={() => actions.setStatus("approved")}>
                Approve
              </Button>
            ) : null}
            <Input
              type="date"
              aria-label="Planned date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              className="h-8 w-40"
            />
            <Button variant="outline" size="sm" disabled={actions.busy || !day} onClick={() => actions.schedule(day)}>
              Schedule
            </Button>
            <select
              aria-label="Published video"
              value={videoId}
              onChange={(e) => setVideoId(e.target.value)}
              className="h-8 max-w-[14rem] rounded-md border bg-background px-2 text-sm"
            >
              <option value="">Published as…</option>
              {videos.map((v) => (
                <option key={v.youtube_video_id} value={v.youtube_video_id}>
                  {formatDate(v.published_at)} — {v.title}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" disabled={actions.busy || !videoId} onClick={() => actions.published(videoId)}>
              Mark published
            </Button>
            <Button variant="ghost" size="sm" disabled={actions.busy} onClick={() => actions.setStatus("rejected")}>
              Reject
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
