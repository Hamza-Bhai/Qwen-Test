"use client";

import { Tabs } from "@/components/ui/tabs";
import { CandidatesTab } from "@/components/studio/competitors/candidates-tab";
import { WatchlistTab } from "@/components/studio/competitors/watchlist-tab";

export function CompetitorsPanel() {
  return (
    <Tabs
      size="sm"
      urlParam="comp"
      defaultId="watchlist"
      tabs={[
        { id: "watchlist", label: "Watchlist", content: <WatchlistTab /> },
        { id: "candidates", label: "Candidates", content: <CandidatesTab /> },
      ]}
    />
  );
}
