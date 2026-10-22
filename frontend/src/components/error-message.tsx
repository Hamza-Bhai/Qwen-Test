"use client";

import { AlertTriangle, KeyRound, ServerCrash, Timer } from "lucide-react";
import { ApiError } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/** Maps an unknown thrown error to an understandable, actionable message. */
export function describeError(error: unknown): {
  title: string;
  message: string;
  hint?: string;
  icon: React.ReactNode;
} {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "network_error":
        return {
          title: "Backend unreachable",
          message: error.message,
          hint: "Start FastAPI in the backend terminal: uvicorn app.main:app --port 8000",
          icon: <ServerCrash className="h-4 w-4" aria-hidden="true" />,
        };
      case "unsupported_url_format":
        return {
          title: "Unsupported channel URL format",
          message: error.message,
          hint: "Use youtube.com/@handle or youtube.com/channel/UC… — legacy /c/ and /user/ URLs are reported instead of guessed.",
          icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
        };
      case "quota_exceeded":
        return {
          title: "YouTube API quota exhausted",
          message: error.message,
          hint: "The daily 10,000-unit quota is used up. Try again after midnight Pacific time.",
          icon: <Timer className="h-4 w-4" aria-hidden="true" />,
        };
      default:
        if (error.status === 404)
          return {
            title: "Channel not found",
            message: error.message,
            hint: "Double-check the handle — the channel may be deleted or private.",
            icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
          };
        if (error.status === 502) {
          const missingKey = /api key/i.test(error.message);
          return {
            title: missingKey ? "Missing YouTube API key" : "YouTube API error",
            message: error.message,
            hint: missingKey
              ? "Set YOUTUBE_API_KEY in backend/.env and restart uvicorn."
              : "The backend could not complete the YouTube Data API call. Check the backend logs.",
            icon: <KeyRound className="h-4 w-4" aria-hidden="true" />,
          };
        }
        if (error.status === 429)
          return {
            title: "Too many requests",
            message: error.message,
            icon: <Timer className="h-4 w-4" aria-hidden="true" />,
          };
        return {
          title: `Request failed (HTTP ${error.status})`,
          message: error.message,
          icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
        };
    }
  }
  return {
    title: "Something went wrong",
    message: error instanceof Error ? error.message : String(error),
    icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
  };
}

export function ErrorMessage({ error }: { error: unknown }) {
  const { title, message, hint, icon } = describeError(error);
  return (
    <Alert variant="destructive">
      {icon}
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {hint ? <p className="mt-1 text-xs opacity-80">{hint}</p> : null}
      </AlertDescription>
    </Alert>
  );
}
