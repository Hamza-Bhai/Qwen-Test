"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { getChannelOverview } from "@/lib/api";
import { ChannelOverviewView } from "@/components/channel-overview";
import { ErrorMessage } from "@/components/error-message";
import { Skeleton } from "@/components/ui/skeleton";

function parseId(raw: string | string[] | undefined): number | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export default function ChannelPage({ params }: { params: { id: string | string[] } }) {
  // Next.js 14 App Router passes dynamic params synchronously.
  const channelId = parseId(params?.id);

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["overview", channelId],
    queryFn: () => getChannelOverview(channelId as number),
    enabled: channelId !== null,
  });

  if (channelId === null) {
    return (
      <div className="space-y-4 pt-8">
        <ErrorMessage error={new Error("Invalid channel id in the URL.")} />
        <BackLink />
      </div>
    );
  }

  return (
    <div className="space-y-6 pt-8">
      <div className="flex items-center justify-between">
        <BackLink />
      </div>

      {isPending ? (
        <div role="status" aria-label="Loading channel overview" className="space-y-4">
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading channel overview…
          </div>
          <Skeleton className="h-40" />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
          <Skeleton className="h-64" />
        </div>
      ) : isError ? (
        <>
          <ErrorMessage error={error} />
          <div className="rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
            This can happen if the backend database was reset or the channel was deleted. Run a new analysis from the{" "}
            <Link href="/" className="text-primary underline-offset-4 hover:underline">home page</Link>.
          </div>
        </>
      ) : (
        <ChannelOverviewView overview={data} />
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to analyzer
    </Link>
  );
}
