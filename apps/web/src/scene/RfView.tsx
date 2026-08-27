import { useEffect, useState } from "react";

import { useLive } from "../data/useLive";
import { IntroCard } from "../ui/IntroCard";
import { LivePanel } from "../ui/LivePanel";
import type { LiveFeedLike } from "../ui/livePanelTypes";
import { RfViewer } from "./RfViewer";

/**
 * Live RF view: the canvas, the instrument panel, and the age readout that ticks between polls.
 *
 * The feed lands once a second; the "live · Ns" age has to advance in between or it reads as
 * frozen. A 1 Hz interval drives just that number -- cheap, and it makes the panel feel like an
 * instrument that is watching rather than a page that loaded once.
 */
export function RfView() {
  const feed = useLive(true);
  const [, tick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const ageSeconds =
    feed.receivedAt !== null ? (performance.now() - feed.receivedAt) / 1000 : null;

  const panelFeed: LiveFeedLike = {
    snapshot: feed.snapshot,
    status: feed.status,
    detail: feed.detail,
    ageSeconds,
  };

  return (
    <>
      <RfViewer snapshot={feed.snapshot} />
      <LivePanel feed={panelFeed} />
      <IntroCard />
    </>
  );
}
