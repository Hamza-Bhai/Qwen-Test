"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Plus, TriangleAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorMessage } from "@/components/error-message";
import { ChannelSwitcher, defaultChannelId } from "@/components/studio/channel-switcher";
import { ChannelOverview } from "@/components/studio/my-channel/channel-overview";
import { CONNECT_URL, listMyChannels } from "@/lib/studio-api";

export const MY_CHANNELS_KEY = ["studio", "my-channels"] as const;

function ConnectButton({ label = "Connect a channel" }: { label?: string }) {
  return (
    <a href={CONNECT_URL} className={buttonVariants({ size: "sm" })}>
      <Plus className="h-4 w-4" aria-hidden="true" />
      {label}
    </a>
  );
}

/** Reads (once) the ?connected= / ?oauth_error= params the OAuth callback adds, then removes them from the URL. */
function useOAuthResult() {
  // The first client render shows a skeleton while channels load, so reading the URL here never changes the
  // server-rendered markup.
  const [result] = useState<{ connected: number | null; error: string | null }>(() => {
    if (typeof window === "undefined") return { connected: null, error: null };
    const params = new URL(window.location.href).searchParams;
    const connected = params.get("connected");
    return { connected: connected ? Number(connected) : null, error: params.get("oauth_error") };
  });
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("connected") && !url.searchParams.has("oauth_error")) return;
    url.searchParams.delete("connected");
    url.searchParams.delete("oauth_error");
    window.history.replaceState(null, "", url.toString());
  }, []);
  return result;
}

export function MyChannelPanel() {
  const oauth = useOAuthResult();
  const [selected, setSelected] = useState<number | null>(null);
  const { data, isPending, isError, error } = useQuery({ queryKey: MY_CHANNELS_KEY, queryFn: listMyChannels });

  const activeId = selected ?? oauth.connected ?? defaultChannelId(data);
  const justConnected = data?.find((c) => c.id === oauth.connected);

  if (isPending) {
    return (
      <div role="status" aria-label="Loading your channels" className="space-y-3">
        <Skeleton className="h-12" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (isError) return <ErrorMessage error={error} />;

  return (
    <div className="space-y-4">
      {oauth.error ? (
        <Alert variant="destructive">
          <TriangleAlert className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Could not connect the channel</AlertTitle>
          <AlertDescription>{oauth.error}</AlertDescription>
        </Alert>
      ) : null}
      {justConnected ? (
        <Alert>
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>{justConnected.title} is connected</AlertTitle>
          <AlertDescription>
            The first pull is running in the background — refresh in a minute. Impressions and CTR arrive 1–2 days
            after connecting, because Google prepares those reports once a day.
          </AlertDescription>
        </Alert>
      ) : null}

      {data.length === 0 ? (
        <Card>
          <CardContent className="space-y-3 py-8 text-sm">
            <p className="font-medium">Connect your YouTube channel to see its private analytics.</p>
            <p className="text-muted-foreground">
              You sign in with Google and pick the channel. The app only asks for read-only access (views, watch time,
              retention, traffic sources, impressions and CTR) and never changes anything on YouTube. You can connect
              several channels — press Connect once per channel.
            </p>
            <p className="text-muted-foreground">
              Google will say &quot;Google hasn&apos;t verified this app&quot;. That is expected for a personal tool: choose
              <span className="font-medium"> Advanced → Go to YT Growth Studio</span>.
            </p>
            <ConnectButton label="Connect with Google" />
          </CardContent>
        </Card>
      ) : (
        <>
          <ChannelSwitcher channels={data} activeId={activeId} onSelect={setSelected}>
            <ConnectButton />
          </ChannelSwitcher>
          {activeId !== null ? <ChannelOverview key={activeId} channelId={activeId} /> : null}
        </>
      )}
    </div>
  );
}
