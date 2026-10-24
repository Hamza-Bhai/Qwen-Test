"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { ChannelSwitcher, NoChannelYet, useChannelChoice } from "@/components/studio/channel-switcher";
import { FindingsList } from "@/components/studio/my-channel/findings-list";
import { formatDateTime } from "@/lib/format";
import { generateAdvice, getAdvisor, type EvidenceRef } from "@/lib/studio-api";

const CONFIDENCE_VARIANT = { high: "default", medium: "secondary", low: "outline" } as const;

export function EvidenceChips({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceRef> }) {
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer select-none">Based on {ids.length} piece{ids.length === 1 ? "" : "s"} of evidence</summary>
      <ul className="mt-2 space-y-1">
        {ids.map((id) => (
          <li key={id}>
            <span className="font-mono text-[11px] text-foreground">{id}</span> — {evidence.get(id)?.text ?? "not in this pack"}
          </li>
        ))}
      </ul>
    </details>
  );
}

function AdvisorView({ channelId }: { channelId: number }) {
  const queryClient = useQueryClient();
  const key = ["studio", "advisor", channelId] as const;
  const { data, isPending, isError, error } = useQuery({ queryKey: key, queryFn: () => getAdvisor(channelId) });
  const generate = useMutation({
    mutationFn: (force: boolean) => generateAdvice(channelId, force),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });

  if (isPending) {
    return (
      <div role="status" aria-label="Loading advice" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (isError) return <ErrorMessage error={error} />;

  const { ai, advice, questions } = data;
  const evidence = new Map((advice?.evidence ?? []).map((e) => [e.id, e]));
  const limitReached = ai.used_today >= ai.cap;

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div className="space-y-1 text-sm">
            <p className="font-medium">Growth advisor</p>
            <p className="text-muted-foreground">
              {ai.configured
                ? `Answers come only from your own numbers, your competitors and YouTube search data — every answer shows its evidence. AI calls today: ${ai.used_today}/${ai.cap}.`
                : "To get written advice, set AI_PROVIDER=claude_cli in backend/.env (uses your Claude subscription through the Claude Code CLI) or add AI_API_KEY for an OpenAI-compatible API, then restart the backend. The rule-based findings below work without it."}
            </p>
            {advice ? (
              <p className="text-xs text-muted-foreground">
                Last advice: {formatDateTime(advice.created_at)} · {advice.model}
              </p>
            ) : null}
          </div>
          {ai.configured ? (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => generate.mutate(false)} disabled={generate.isPending || limitReached}>
                {generate.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="h-4 w-4" aria-hidden="true" />
                )}
                {advice ? "Update advice" : "Get advice"}
              </Button>
              {advice ? (
                <Button variant="outline" size="sm" onClick={() => generate.mutate(true)} disabled={generate.isPending || limitReached}>
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  Ask again
                </Button>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
      {generate.isError ? <ErrorMessage error={generate.error} /> : null}

      {advice ? (
        <div className="grid gap-4 md:grid-cols-2">
          {advice.answers.map((a) => (
            <Card key={a.question}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-start justify-between gap-2 text-base">
                  {questions[a.question] ?? a.question}
                  <Badge variant={CONFIDENCE_VARIANT[a.confidence]}>{a.confidence}</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="whitespace-pre-line text-sm">{a.answer}</p>
                <EvidenceChips ids={a.evidence_ids} evidence={evidence} />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">What the numbers say (rule-based)</h2>
        <FindingsList findings={data.findings} />
      </section>
    </div>
  );
}

export function DashboardPanel() {
  const { data, isPending, isError, error, activeId, setSelected } = useChannelChoice();
  if (isPending) return <Skeleton className="h-12" />;
  if (isError) return <ErrorMessage error={error} />;
  if (data.length === 0 || activeId === null) return <NoChannelYet what="the advice" />;
  return (
    <div className="space-y-4">
      {data.length > 1 ? <ChannelSwitcher channels={data} activeId={activeId} onSelect={setSelected} /> : null}
      <AdvisorView key={activeId} channelId={activeId} />
    </div>
  );
}
