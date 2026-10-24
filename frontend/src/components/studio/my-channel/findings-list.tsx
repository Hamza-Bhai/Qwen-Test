"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Finding } from "@/lib/studio-api";
import { cn } from "@/lib/utils";

const ORDER: Finding["status"][] = ["issue", "good", "info", "inconclusive"];

const STATUS_STYLE: Record<Finding["status"], { label: string; className: string }> = {
  issue: { label: "Fix this", className: "border-l-destructive" },
  good: { label: "Working", className: "border-l-primary" },
  info: { label: "Insight", className: "border-l-muted-foreground" },
  inconclusive: { label: "Not enough data", className: "border-l-border opacity-80" },
};

type EvidenceVideo = { video_id: string; title?: string | null; [key: string]: unknown };

function isVideoList(value: unknown): value is EvidenceVideo[] {
  return Array.isArray(value) && value.every((v) => typeof v === "object" && v !== null && "video_id" in v);
}

function formatValue(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(2);
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence);
  if (entries.length === 0) return null;
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer select-none">Evidence</summary>
      <dl className="mt-2 space-y-2">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt className="font-medium text-foreground">{key.replaceAll("_", " ")}</dt>
            <dd>
              {isVideoList(value) ? (
                value.length === 0 ? (
                  "—"
                ) : (
                  <ul className="space-y-1">
                    {value.map((v) => (
                      <li key={v.video_id}>
                        <a
                          href={`https://www.youtube.com/watch?v=${v.video_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:text-primary"
                        >
                          {v.title ?? v.video_id}
                        </a>{" "}
                        <span className="tabular-nums">
                          {Object.entries(v)
                            .filter(([k]) => k !== "video_id" && k !== "title")
                            .map(([k, val]) => `${k.replaceAll("_", " ")} ${formatValue(val)}`)
                            .join(" · ")}
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              ) : (
                <span className="tabular-nums break-words">{formatValue(value)}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function FindingsList({ findings }: { findings: Finding[] }) {
  const sorted = [...findings].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  return (
    <ul className="space-y-2">
      {sorted.map((f) => (
        <li key={f.code}>
          <Card className={cn("border-l-4", STATUS_STYLE[f.status].className)}>
            <CardContent className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">{f.title}</p>
                <Badge variant={f.status === "issue" ? "destructive" : "secondary"}>{STATUS_STYLE[f.status].label}</Badge>
                {f.status !== "inconclusive" ? (
                  <span className="text-xs text-muted-foreground">confidence: {f.confidence}</span>
                ) : null}
              </div>
              <p className="text-sm">{f.summary}</p>
              {f.action ? <p className="text-sm text-muted-foreground">→ {f.action}</p> : null}
              <Evidence evidence={f.evidence} />
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}
