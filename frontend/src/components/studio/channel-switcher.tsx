"use client";

import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { listMyChannels, type MyChannel } from "@/lib/studio-api";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<MyChannel["status"], string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  needs_reconnect: "Reconnect needed",
};

/** The channel the page should show by default: the first one still connected. */
export function defaultChannelId(channels: MyChannel[] | undefined): number | null {
  return channels?.find((c) => c.status !== "disconnected")?.id ?? channels?.[0]?.id ?? null;
}

export function ChannelSwitcher({
  channels,
  activeId,
  onSelect,
  children,
}: {
  channels: MyChannel[];
  activeId: number | null;
  onSelect: (id: number) => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Connected channels">
      {channels.map((c) => (
        <button
          key={c.id}
          type="button"
          role="tab"
          aria-selected={c.id === activeId}
          onClick={() => onSelect(c.id)}
          className={cn(
            "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors",
            c.id === activeId ? "border-primary bg-primary/10 font-medium" : "hover:bg-muted"
          )}
        >
          {c.thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={c.thumbnail_url} alt="" className="h-5 w-5 rounded-full" />
          ) : null}
          {c.title}
          {c.status !== "connected" ? (
            <Badge variant={c.status === "needs_reconnect" ? "destructive" : "outline"}>{STATUS_LABEL[c.status]}</Badge>
          ) : null}
        </button>
      ))}
      {children}
    </div>
  );
}

/** Connected channels plus the one the user picked (Dashboard and Ideas & Plan share this). */
export function useChannelChoice() {
  const [selected, setSelected] = useState<number | null>(null);
  const query = useQuery({ queryKey: ["studio", "my-channels"], queryFn: listMyChannels });
  return { ...query, activeId: selected ?? defaultChannelId(query.data), setSelected };
}

export function NoChannelYet({ what }: { what: string }) {
  return (
    <Card>
      <CardContent className="py-8 text-sm text-muted-foreground">
        Connect your channel in the <span className="font-medium text-foreground">My channel</span> tab first — {what}{" "}
        is built from its private analytics.
      </CardContent>
    </Card>
  );
}
