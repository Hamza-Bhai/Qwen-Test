"use client";

import { Tabs } from "@/components/ui/tabs";
import { InsightsTab } from "@/components/studio/competitors/insights-tab";
import { NewUploadsTab } from "@/components/studio/competitors/new-uploads-tab";
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
        { id: "new-uploads", label: "New uploads", content: <NewUploadsTab /> },
        { id: "insights", label: "Insights", content: <InsightsTab /> },
      ]}
    />
  );
}
