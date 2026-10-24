/** Human-friendly formatting helpers shared across the dashboard. */

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

export function formatCompact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

// Fixed options so server- and client-rendered output are identical (no hydration mismatch).
const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "2-digit" };
const DATETIME_OPTS: Intl.DateTimeFormatOptions = { ...DATE_OPTS, hour: "2-digit", minute: "2-digit" };

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", DATE_OPTS).format(d);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", DATETIME_OPTS).format(d);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export function formatRatio(r: number | null | undefined): string {
  if (r === null || r === undefined) return "—";
  return `${(r * 100).toFixed(2)}%`;
}

const COUNTRY_NAMES: Record<string, string> = {
  US: "United States", GB: "United Kingdom", IN: "India", PK: "Pakistan",
  CA: "Canada", AU: "Australia", DE: "Germany", FR: "France", BR: "Brazil",
  MX: "Mexico", ES: "Spain", IT: "Italy", NL: "Netherlands", SE: "Sweden",
  JP: "Japan", KR: "South Korea", TR: "Turkey", AE: "UAE", SA: "Saudi Arabia",
  EG: "Egypt", NG: "Nigeria", ZA: "South Africa", ID: "Indonesia", PH: "Philippines",
};

export function countryName(code: string | null | undefined): string {
  if (!code) return "—";
  return COUNTRY_NAMES[code.toUpperCase()] ?? code;
}

/** Signed percentage for baseline comparisons, e.g. +12.3% / -8.1%. */
export function formatPct(p: number | null | undefined, digits = 1): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  const sign = p > 0 ? "+" : "";
  return `${sign}${p.toFixed(digits)}%`;
}

/** Plain decimal to one place (views/day, uploads/month). */
export function formatDecimal(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
