"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { deleteChannel } from "@/lib/api";
import type { Channel } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { ErrorMessage } from "@/components/error-message";

/** Asks what to do with a channel's data before removing it from the list:
 *  keep it (adding the channel again restores everything) or delete it all. */
export function RemoveChannelDialog({ channel, onClose }: { channel: Channel; onClose: () => void }) {
  const queryClient = useQueryClient();
  const keepRef = useRef<HTMLButtonElement>(null);

  const remove = useMutation({
    mutationFn: (keepData: boolean) => deleteChannel(channel.id, { keepData }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channels"] });
      onClose();
    },
  });

  // Focus the safe option first and let Escape close the dialog.
  useEffect(() => {
    keepRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !remove.isPending) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, remove.isPending]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !remove.isPending) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="remove-channel-title"
        className="w-full max-w-md rounded-lg border bg-card p-5 text-card-foreground shadow-lg"
      >
        <h2 id="remove-channel-title" className="text-base font-semibold">
          Remove “{channel.title}”?
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Choose what happens to this channel&apos;s saved videos, history and audit data.
        </p>

        <div className="mt-4 space-y-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium">Keep my data</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The channel disappears from this list, but nothing is lost. If you add it again, everything shows up
              as before. It is not synced while removed.
            </p>
            <Button
              ref={keepRef}
              size="sm"
              className="mt-2"
              disabled={remove.isPending}
              onClick={() => remove.mutate(true)}
            >
              {remove.isPending && remove.variables === true ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              Remove, keep data
            </Button>
          </div>

          <div className="rounded-md border border-destructive/40 p-3">
            <p className="text-sm font-medium">Delete everything</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Permanently erases this channel&apos;s videos, view history and audit history. This cannot be undone.
              Adding it again starts from scratch.
            </p>
            <Button
              size="sm"
              variant="destructive"
              className="mt-2"
              disabled={remove.isPending}
              onClick={() => remove.mutate(false)}
            >
              {remove.isPending && remove.variables === false ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              Delete all data
            </Button>
          </div>
        </div>

        {remove.isError ? (
          <div className="mt-3">
            <ErrorMessage error={remove.error} />
          </div>
        ) : null}

        <div className="mt-4 flex justify-end">
          <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
