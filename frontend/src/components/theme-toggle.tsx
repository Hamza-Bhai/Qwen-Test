"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useCallback, useEffect, useSyncExternalStore } from "react";

import { THEME_STORAGE_KEY } from "@/lib/theme";

type Theme = "light" | "dark" | "system";

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light theme", icon: Sun },
  { value: "system", label: "Use system theme", icon: Monitor },
  { value: "dark", label: "Dark theme", icon: Moon },
];

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage can be blocked (private mode); fall back to following the system.
  }
  return "system";
}

function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

const THEME_EVENT = "yt-growth-theme-change";

function subscribe(onChange: () => void) {
  // Another tab changed the theme: apply it here too, then re-read it.
  const onStorage = () => {
    applyTheme(readStoredTheme());
    onChange();
  };
  window.addEventListener(THEME_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Light / System / Dark switch. The choice is remembered in localStorage and
 *  applied before first paint by the inline script in the root layout. */
export function ThemeToggle() {
  // The server cannot know the saved theme, so it renders null (nothing
  // highlighted); the client reads the real value after hydration. This avoids
  // a hydration mismatch without setting state inside an effect.
  const theme = useSyncExternalStore<Theme | null>(subscribe, readStoredTheme, () => null);

  // While following the system, react to the OS switching themes live.
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const choose = useCallback((next: Theme) => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Not persisted, but the theme still applies for this visit.
    }
    applyTheme(next);
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);

  return (
    <div role="group" aria-label="Color theme" className="inline-flex items-center rounded-md border p-0.5">
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => choose(value)}
            aria-label={label}
            aria-pressed={active}
            title={label}
            className={`inline-flex h-7 w-7 items-center justify-center rounded transition-colors ${
              active ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
