import { useEffect, useRef, useState } from "react";

import { parseSnapshot, type LiveSnapshot } from "../types/live";

export type LiveStatus = "waiting" | "live" | "stale";

export interface LiveFeed {
  snapshot: LiveSnapshot | null;
  status: LiveStatus;
  /** Human-readable reason we are not live. Shown verbatim in the panel. */
  detail: string | null;
  /** performance.now() of the last successful parse, for the age readout. */
  receivedAt: number | null;
}

const POLL_MS = 1000;

/**
 * Polls /live.json.
 *
 * setState at 1 Hz for a dozen rows is the right amount of React. The prohibition in
 * data/store.ts is about per-entity state at frame rate with hundreds of entities; here
 * one object lands once a second and every consumer wants to re-render when it does.
 *
 * The file legitimately does not exist until the collector runs, so a 404 is a normal
 * state and not an error. Polling never stops: the collector may start at any time.
 */
export function useLive(enabled: boolean): LiveFeed {
  const [feed, setFeed] = useState<LiveFeed>({
    snapshot: null,
    status: "waiting",
    detail: "no /live.json yet",
    receivedAt: null,
  });
  // Read inside the poll without making it a dependency, so the interval is never rebuilt.
  const hadSnapshot = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let cancelled = false;

    const fail = (detail: string) => {
      if (cancelled) return;
      setFeed((prev) => ({ ...prev, status: hadSnapshot.current ? "stale" : "waiting", detail }));
    };

    const poll = async () => {
      try {
        // Cache-bust: a dev server will happily 304 a file the collector just rewrote.
        const res = await fetch(`/live.json?t=${Date.now()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!res.ok) return fail(`no /live.json yet (HTTP ${res.status})`);

        const snapshot = parseSnapshot(await res.json());
        if (!snapshot) return fail("/live.json is not a live snapshot");
        if (cancelled) return;

        hadSnapshot.current = true;
        setFeed({ snapshot, status: "live", detail: null, receivedAt: performance.now() });
      } catch (err) {
        if (controller.signal.aborted) return;
        fail(err instanceof SyntaxError ? "/live.json is not valid JSON (partial write?)" : "/live.json unreachable");
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [enabled]);

  return feed;
}
