"use client";

import { Sparkles } from "lucide-react";
import { DashboardPanel } from "@/components/studio/dashboard/dashboard-panel";
import { CompetitorsPanel } from "@/components/studio/competitors/competitors-panel";
import { IdeasPanel } from "@/components/studio/ideas/ideas-panel";
import { MyChannelPanel } from "@/components/studio/my-channel/my-channel-panel";
import { SettingsPanel } from "@/components/studio/settings-panel";
import { Tabs } from "@/components/ui/tabs";

export default function StudioPage() {
  return (
    <div className="space-y-6 pt-8">
      <div className="space-y-1">
        <p className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          Growth Studio — personal
        </p>
        <h1 className="text-2xl font-bold tracking-tight">Growth Studio</h1>
        <p className="text-sm text-muted-foreground">
          Competitors, your private analytics and daily video ideas — built on real data, never invented numbers.
        </p>
      </div>
      <Tabs
        urlParam="tab"
        defaultId="settings"
        tabs={[
          { id: "dashboard", label: "Dashboard", content: <DashboardPanel /> },
          { id: "ideas", label: "Ideas & Plan", content: <IdeasPanel /> },
          { id: "my-channel", label: "My channel", content: <MyChannelPanel /> },
          { id: "competitors", label: "Competitors", content: <CompetitorsPanel /> },
          { id: "settings", label: "Settings", content: <SettingsPanel /> },
        ]}
      />
    </div>
  );
}
