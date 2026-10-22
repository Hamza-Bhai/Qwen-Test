import Link from "next/link";
import { PlayCircle, BarChart3 } from "lucide-react";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <PlayCircle className="h-6 w-6 text-primary" aria-hidden="true" />
          <span>
            YT Growth <span className="text-primary">Intelligence</span>
          </span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-1">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <BarChart3 className="h-4 w-4" aria-hidden="true" />
            Analyze
          </Link>
        </nav>
      </div>
    </header>
  );
}
