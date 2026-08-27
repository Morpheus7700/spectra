"""Which BSSIDs are the same physical radio.

Platform-neutral on purpose: the Windows adapter needs it to enforce the collection-path
filter, and the survey tooling needs it to avoid double-weighting one antenna, but it is pure
string arithmetic and must not sit behind a `sys.platform` guard where Linux CI cannot reach
it.
"""

from __future__ import annotations

from collections.abc import Iterable

MULTI_BSS_MASK = 0xFC
"""Low bits of the final octet that vary between BSSes on one radio."""


def radio_key(bssid: str) -> tuple[str, ...]:
    """Identity of the physical radio behind a BSSID. Equal keys mean one antenna.

    One box presents several BSSIDs: its 2.4 and 5 GHz radios differ only in the last octet
    (`…af:32:a0` / `…af:32:a1`), and a guest or mesh BSS differs only in the
    locally-administered bit of the first (`ac:…` / `ae:…`). Counting those as independent
    manufactures phantom anchors at zero baseline and double-weights one radio in any k-NN
    metric -- the degenerate geometry R8 exists to catch, arriving disguised as a good fix.

    So: the last two octets, with the low two bits of the final one masked.

    That is looser than it looks like it should be, and the looseness is measured rather than
    chosen. A stricter key over the last *four* octets correctly pairs `…af:32:a0` with
    `…af:32:a1`, but fails on an observed neighbour box whose guest BSS derives from
    `c0:00:00:10:cf:85` as `c2:00:00:11:cf:85` -- changing octet 3 as well as the
    locally-administered bit. Vendors do not agree on a derivation rule, so matching the tail
    is the only thing that holds across them.

    # ponytail: 14 bits of match, so with ~15 BSSIDs in range the chance of two unrelated
    # radios colliding is under 1%. The cost of being wrong is asymmetric and worth naming: a
    # false merge deletes a feature quietly, and in the collection filter it would admit a
    # neighbour's radio. Both are visible in the admitted list, which is why every caller
    # prints what it matched instead of merging silently. Upgrade to OUI lookup plus
    # beacon-IE correlation if a collision is ever actually observed.
    """
    octets = bssid.lower().split(":")
    if len(octets) != 6:
        return (bssid.lower(),)
    try:
        last = int(octets[5], 16) & MULTI_BSS_MASK
    except ValueError:
        return (bssid.lower(),)
    return (octets[4], f"{last:02x}")


def same_radio(bssid: str, reference: str) -> bool:
    """Whether two BSSIDs are the same physical radio. See `radio_key`."""
    return radio_key(bssid) == radio_key(reference)


def candidate_radio_groups(bssids: Iterable[str]) -> list[list[str]]:
    """Group BSSIDs that are probably one radio, for a human to confirm.

    Returns *candidates* and never merges anything itself -- collapsing two genuinely separate
    neighbours would quietly delete a feature, which is harder to notice than a duplicate.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for bssid in bssids:
        groups.setdefault(radio_key(bssid), []).append(bssid)
    return [sorted(g) for g in groups.values() if len(g) > 1]
