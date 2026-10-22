"use client";

import Link from "next/link";
import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Users } from "lucide-react";
import { listChannels } from "@/lib/api";
import { formatCompact, formatDate } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";

export function ChannelsList() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["channels"],
    queryFn: listChannels,
  });

  if (isPending) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" role="status" aria-label="Loading saved channels">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
    );
  }

  if (isError) return <ErrorMessage error={error} />;

  if (data.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="py-10 text-center">
          <p className="text-sm font-medium">No channels analyzed yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste a YouTube channel URL above and click “Analyze Channel” to get started.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.map((ch) => (
        <li key={ch.id}>
          <Link href={`/channels/${ch.id}`} className="group block">
            <Card className="transition group-hover:border-primary/40 group-hover:shadow-md">
              <CardContent className="flex items-center gap-3 p-4">
                {ch.thumbnail_url ? (
                  <Image src={ch.thumbnail_url} alt="" width={48} height={48} className="h-12 w-12 shrink-0 rounded-full border object-cover" />
                ) : (
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border bg-muted">
                    <Users className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold group-hover:text-primary">{ch.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {ch.subscriber_count === null ? "Subs private" : `${formatCompact(ch.subscriber_count)} subs`} ·{" "}
                    {formatCompact(ch.video_count)} videos · added {formatDate(ch.last_synced_at)}
                  </p>
                </div>
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden="true" />
              </CardContent>
            </Card>
          </Link>
        </li>
      ))}
    </ul>
  );
}
