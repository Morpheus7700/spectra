import { useState } from "react";

/**
 * The plain-language layer. This scene is legible to an engineer at a glance and opaque to
 * everyone else, so it has to explain itself the moment it opens -- no jargon, no dBm, no
 * "shells". It says what the dot is, what the spheres are, and why they are spheres.
 *
 * Shown on first open, dismissable, and remembered so it does not nag the owner. A persistent
 * "What am I seeing?" chip reopens it, because the next person shown the demo has not read it.
 */

const SEEN_KEY = "spectra.intro.dismissed";

function wasDismissed(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function IntroCard() {
  const [open, setOpen] = useState(!wasDismissed());

  const dismiss = () => {
    setOpen(false);
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* private window -- fine, it just shows again next time */
    }
  };

  if (!open) {
    return (
      <button className="intro-chip" onClick={() => setOpen(true)}>
        What am I seeing?
      </button>
    );
  }

  return (
    <div className="intro-scrim" onClick={dismiss}>
      <div className="intro-card" onClick={(e) => e.stopPropagation()}>
        <div className="intro-eyebrow">Spectra · Live</div>
        <h1 className="intro-title">The WiFi around this computer, in 3D</h1>

        <p className="intro-lede">
          Your computer is the bright dot in the middle. Every glowing sphere is a WiFi
          router it can hear — yours and the neighbours&rsquo;.
        </p>

        <ul className="intro-points">
          <li>
            <span className="intro-key intro-key--own" />
            <span>
              <strong>Green is your own WiFi.</strong> The rest are other routers nearby.
            </span>
          </li>
          <li>
            <span className="intro-key intro-key--ring" />
            <span>
              <strong>Each sphere&rsquo;s size is a distance.</strong> With one antenna the
              computer can tell how <em>far</em> a router is, but not which direction — so
              &ldquo;8 metres away&rdquo; is drawn as a bubble 8 metres across in every
              direction.
            </span>
          </li>
          <li>
            <span className="intro-key intro-key--fuzz" />
            <span>
              <strong>Fuzzier means less certain.</strong> WiFi distance is rough, so the
              bubble is a best guess, not a pinpoint.
            </span>
          </li>
        </ul>

        <p className="intro-foot">
          Drag to orbit. The panel on the left lists each one. When the signal is too weak to
          guess a distance honestly, it&rsquo;s set aside rather than made up.
        </p>

        <button className="intro-go" onClick={dismiss}>
          Explore
        </button>
      </div>
    </div>
  );
}
