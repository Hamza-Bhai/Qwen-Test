import { TrendingUp } from "lucide-react";
import { AnalyzeForm } from "@/components/analyze-form";
import { ChannelsList } from "@/components/channels-list";

export default function HomePage() {
  return (
    <div className="space-y-10 pt-8 sm:pt-12">
      {/* Hero */}
      <section className="space-y-3 text-center sm:text-left">
        <p className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
          <TrendingUp className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          Phase 1 — Channel lookup &amp; dashboard
        </p>
        <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
          Grow smarter with <span className="text-primary">real YouTube data</span>
        </h1>
        <p className="mx-auto max-w-2xl text-sm text-muted-foreground sm:mx-0 sm:text-base">
          Paste any public YouTube channel URL. YT Growth Intelligence resolves it through the official
          YouTube Data API and shows subscribers, total views, video count, join date and recent uploads —
          no mock numbers, ever.
        </p>
      </section>

      {/* Analyzer */}
      <AnalyzeForm />

      {/* Saved channels */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">Your analyzed channels</h2>
        <ChannelsList />
      </section>
    </div>
  );
}
