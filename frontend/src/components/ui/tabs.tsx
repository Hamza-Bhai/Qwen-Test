"use client";

import { createContext, useContext, useId, useRef, useState, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

export interface TabItem {
  id: string;
  label: string;
  badge?: string | number;
  content: React.ReactNode;
}

// True inside a "full data" panel: nested Tabs then render every panel stacked.
const ShowAllContext = createContext(false);

const ALL_ID = "__all__";

const subscribeToNothing = () => () => {};

/**
 * Minimal accessible tabs (WAI-ARIA tabs pattern, arrow/Home/End keys).
 * Only the active panel is mounted, so charts always measure a visible container.
 */
export function Tabs({
  tabs,
  size = "default",
  className,
  allLabel,
  defaultId,
  urlParam,
}: {
  tabs: TabItem[];
  size?: "default" | "sm";
  className?: string;
  /** Adds a first tab that shows every tab's content stacked (nested Tabs too). */
  allLabel?: string;
  defaultId?: string;
  /** Keeps the active tab in this URL query param so a refresh restores it. */
  urlParam?: string;
}) {
  const showAll = useContext(ShowAllContext);
  // The URL is read through useSyncExternalStore: the server snapshot is "" so
  // server and client markup match, then the client snapshot picks up ?tab=.
  // Later changes use replaceState (no history flood) and are mirrored in `selected`.
  const search = useSyncExternalStore(
    subscribeToNothing,
    () => window.location.search,
    () => ""
  );
  const [selected, setSelected] = useState<string | null>(null);
  const fromUrl = urlParam ? new URLSearchParams(search).get(urlParam) : null;
  const wanted = selected ?? fromUrl;
  const baseId = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  function select(id: string) {
    setSelected(id);
    if (!urlParam) return;
    const url = new URL(window.location.href);
    url.searchParams.set(urlParam, id);
    window.history.replaceState(window.history.state, "", url);
  }

  const stacked = <>{tabs.map((t) => <div key={t.id} className="space-y-6">{t.content}</div>)}</>;
  if (showAll) return <div className={cn("space-y-6", className)}>{stacked}</div>;

  const all: TabItem[] = allLabel
    ? [{ id: ALL_ID, label: allLabel, content: <ShowAllContext.Provider value>{stacked}</ShowAllContext.Provider> }]
    : [];
  const items = [...all, ...tabs];
  // An unknown id (e.g. a stale ?tab= value) falls back to the default tab, not the first one.
  const current =
    items.find((t) => t.id === wanted) ?? items.find((t) => t.id === defaultId) ?? items[0];
  if (!current) return null;

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else return;
    e.preventDefault();
    select(items[next].id);
    refs.current[items[next].id]?.focus();
  }

  return (
    <div className={cn("space-y-5", className)}>
      <div
        role="tablist"
        aria-orientation="horizontal"
        className={cn(
          "flex flex-wrap gap-1 border-b",
          size === "sm" && "gap-0.5"
        )}
      >
        {items.map((t, i) => {
          const selected = t.id === current.id;
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(t.id)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={cn(
                "-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm",
                selected
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              {t.label}
              {t.badge !== undefined ? (
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                  {t.badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`${baseId}-panel-${current.id}`}
        aria-labelledby={`${baseId}-tab-${current.id}`}
        tabIndex={0}
        className="space-y-6 focus-visible:outline-none"
      >
        {current.content}
      </div>
    </div>
  );
}
