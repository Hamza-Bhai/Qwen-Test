"use client";

import { Tabs } from "@/components/ui/tabs";
import { WatchlistTab } from "@/components/studio/competitors/watchlist-tab";

export function CompetitorsPanel() {
  return (
    <Tabs
      size="sm"
      urlParam="comp"
      defaultId="watchlist"
      tabs={[{ id: "watchlist", label: "Watchlist", content: <WatchlistTab /> }]}
    />
  );
}
