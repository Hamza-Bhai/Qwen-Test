"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search, Sparkles } from "lucide-react";
import { analyzeChannel } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";

const EXAMPLES = ["https://www.youtube.com/@mkbhd", "https://youtube.com/channel/UC8butISFwT-Wl7EV0hUK0BQ"];

export function AnalyzeForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: analyzeChannel,
    onSuccess: (data) => {
      // Seed the overview cache so the results page renders instantly.
      queryClient.setQueryData(["overview", data.channel.id], data);
      queryClient.invalidateQueries({ queryKey: ["channels"] });
      router.push(`/channels/${data.channel.id}`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (trimmed.length < 2) {
      setInputError("Please paste a YouTube channel URL, @handle, or channel ID.");
      return;
    }
    setInputError(null);
    mutation.mutate(trimmed);
  }

  return (
    <Card className="overflow-hidden">
      <CardContent className="space-y-4 p-6 sm:p-8">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row" noValidate={false}>
          <div className="flex-1">
            <label htmlFor="channel-url" className="sr-only">
              YouTube channel URL
            </label>
            <Input
              id="channel-url"
              name="channel-url"
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://www.youtube.com/@yourfavouritechannel"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={mutation.isPending}
              aria-invalid={inputError ? true : undefined}
              aria-describedby={inputError ? "url-error" : undefined}
              className="h-11"
            />
            {inputError ? (
              <p id="url-error" role="alert" className="mt-1.5 text-sm text-destructive">
                {inputError}
              </p>
            ) : null}
          </div>
          <Button type="submit" size="lg" disabled={mutation.isPending} className="h-11 shrink-0 gap-2">
            {mutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Analyzing…
              </>
            ) : (
              <>
                <Search className="h-4 w-4" aria-hidden="true" /> Analyze Channel
              </>
            )}
          </Button>
        </form>

        <p className="text-xs text-muted-foreground">
          Supported: <code className="rounded bg-muted px-1 py-0.5">youtube.com/@handle</code>,{" "}
          <code className="rounded bg-muted px-1 py-0.5">youtube.com/channel/UC…</code>, bare{" "}
          <code className="rounded bg-muted px-1 py-0.5">@handle</code> or{" "}
          <code className="rounded bg-muted px-1 py-0.5">UC…</code> IDs. Legacy{" "}
          <code className="rounded bg-muted px-1 py-0.5">/c/</code> and <code className="rounded bg-muted px-1 py-0.5">/user/</code>{" "}
          URLs are reported instead of guessed.
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setUrl(ex)}
              className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Sparkles className="h-3 w-3" aria-hidden="true" />
              {ex.replace("https://", "")}
            </button>
          ))}
        </div>

        {mutation.isPending ? (
          <div className="space-y-3 pt-2" role="status" aria-live="polite">
            <p className="text-sm text-muted-foreground">
              Resolving channel via the YouTube Data API, syncing recent videos and saving an analysis snapshot…
            </p>
            <div className="flex items-center gap-4">
              <Skeleton className="h-20 w-20 rounded-full" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          </div>
        ) : null}

        {mutation.isError ? (
          <div aria-live="assertive">
            <ErrorMessage error={mutation.error} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
