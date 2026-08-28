"""tools/domain.py - shared helpers for Track A (outreach.py) and Track B
(upgrade.py): the offer catalogue, date arithmetic and the pieces of the
matching/pricing logic both tracks use the same way.

Nothing here talks to a store or an adapter - pure functions over plain data,
so every rule is a one-line unit test. See docs/how-it-works.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from core.adapters.base import Reservation
from core.config import Settings


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------
def parse_date(value: str) -> date:
    """``YYYY-MM-DD`` -> :class:`date`. Raises ``ValueError`` with the bad string."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def iso(d: date) -> str:
    return d.isoformat()


def nights_stayed(check_in: str, check_out: str) -> list[str]:
    """Every date the room is occupied: ``check_in`` up to, not including, ``check_out``."""
    start, end = parse_date(check_in), parse_date(check_out)
    out, cur = [], start
    while cur < end:
        out.append(iso(cur))
        cur += timedelta(days=1)
    return out


def nights(check_in: str, check_out: str) -> int:
    return len(nights_stayed(check_in, check_out))


def days_out(today: date, check_in: str) -> int:
    """Whole days between ``today`` and the arrival date. Negative = already arrived."""
    return (parse_date(check_in) - today).days


# --------------------------------------------------------------------------
# the offer catalogue - config, not a fixture (docs/how-it-works.md)
# --------------------------------------------------------------------------
@dataclass
class Offer:
    id: str
    title: str
    price: float
    price_unit: str = "one-off"   # one-off | per person | per day
    margin_pct: float = 0.0
    match_keys: list[str] = field(default_factory=list)


#: the two offers a blank-profile guest gets - "nothing invented" (docs/how-it-works.md).
GENERIC_OFFER_IDS = ("of-welcome-table", "of-transfer")


def load_offers(settings: Settings) -> list[Offer]:
    raw = settings.agent_get("offers", []) or []
    out = []
    for o in raw:
        out.append(Offer(
            id=str(o.get("id", "")), title=str(o.get("title", "")),
            price=float(o.get("price", 0) or 0),
            price_unit=str(o.get("price_unit", "one-off")),
            margin_pct=float(o.get("margin_pct", 0) or 0),
            match_keys=[str(k) for k in (o.get("match_keys") or [])],
        ))
    return out


def offer_value(offer: Offer, nights_count: int) -> float:
    """What the guest actually pays for this offer over the stay."""
    if offer.price_unit == "per person":
        return offer.price * 2
    if offer.price_unit == "per day":
        return offer.price * min(max(nights_count, 1), 3)
    return offer.price


def price_label(offer: Offer) -> str:
    if offer.price == 0:
        return "with our compliments"
    if offer.price_unit == "per person":
        return f"EUR {offer.price:.0f} per person"
    if offer.price_unit == "per day":
        return f"EUR {offer.price:.0f} per day"
    return f"EUR {offer.price:.0f}"


# --------------------------------------------------------------------------
# guest profile signal keys (Track A's matcher; ported from specs/upsell-ai.md)
# --------------------------------------------------------------------------
def signal_keys(res: Reservation) -> set[str]:
    extra = res.extra or {}
    profile = extra.get("profile") or {}
    keys: set[str] = set()

    occasion = extra.get("occasion")
    if occasion == "anniversary":
        keys |= {"anniversary", "couples"}
    elif occasion == "honeymoon":
        keys |= {"honeymoon", "couples", "spa"}
    elif occasion == "birthday":
        keys |= {"birthday", "spa"}

    tier = extra.get("tier")
    if tier == "vip":
        keys.add("vip")
    elif tier == "returning":
        keys.add("returning")

    for flag, key in (("dog", "dog"), ("baby", "baby"), ("work", "remote_work"),
                      ("spa", "spa"), ("transfer", "transfer")):
        if profile.get(flag):
            keys.add(key)
    diet = str(profile.get("diet", "")).lower()
    if "vegetarian" in diet:
        keys.add("vegetarian")

    # Only an explicit party description or a real child count counts - a
    # bare "2 adults" on the booking record is not a preference anyone
    # stated, so it must never invent a "couples" pitch on its own (the
    # "nothing invented" guarantee - see docs/how-it-works.md step 6).
    party_text = str(extra.get("party") or "").lower()
    has_children = "children" in party_text or bool(res.children) or bool(profile.get("kids"))
    if has_children:
        keys.add("family")
    elif "2 adults" in party_text:
        keys.add("couples")

    return keys


def because_for(res: Reservation, key: str) -> str:
    """One sentence naming the fact behind a match - the demo's proof point."""
    extra = res.extra or {}
    profile = extra.get("profile") or {}
    if key == "dog":
        return profile.get("dog") or "Traveling with a dog"
    if key == "baby":
        note = profile.get("baby") or "a baby"
        request = profile.get("request", "")
        return f"Traveling with {note}" + (f" - {request}" if request else "")
    if key == "remote_work":
        return profile.get("work") or "Working remotely during the stay"
    if key == "spa":
        return profile.get("spa") or "Showed interest in the spa before arriving"
    if key == "vegetarian":
        return f"{profile.get('diet')} - the menu fits, no asking required"
    if key in ("anniversary", "birthday"):
        article = "an" if key == "anniversary" else "a"
        return extra.get("occasion_note") or f"This stay marks {article} {key}"
    if key == "honeymoon":
        return "Honeymoon stay - the couples' signature is the natural fit"
    if key == "vip":
        return profile.get("stays") or "VIP guest, greeted like one"
    if key == "returning":
        return "Returning guest - greeted like one"
    if key == "transfer":
        return profile.get("transfer") or "Has used the transfer before"
    if key == "family":
        return profile.get("kids") or "Traveling with children"
    if key == "couples":
        return "Booked as a couple"
    return "Matched to the guest profile"


def repeat_offers(res: Reservation, offers: list[Offer], cap: int = 2) -> list[tuple[Offer, str]]:
    """Offers this guest has bought before - guard-exempt, "they've paid for it already"."""
    history = (res.extra or {}).get("history") or []
    taken_titles = [t.lower() for h in history for t in (h.get("upsells_taken") or [])]
    out: list[tuple[Offer, str]] = []
    for offer in offers:
        if len(out) >= cap:
            break
        title = offer.title.lower()
        for h in history:
            for t in (h.get("upsells_taken") or []):
                tl = t.lower()
                if tl == title or tl in title or title in tl:
                    when = h.get("when", "a previous stay")
                    ref = h.get("ref", "")
                    out.append((offer, f"Took the {offer.title} {when}"
                                       f"{f' ({ref})' if ref else ''} - "
                                       "repeat guests rebook what they loved"))
                    break
            else:
                continue
            break
    return out


def nightly_rate(res: Reservation) -> float:
    """What this guest is actually paying per night - never a hard-coded ladder."""
    n = nights(res.check_in, res.check_out) or 1
    return (res.total or 0.0) / n


def match_offers(res: Reservation, offers: list[Offer], *, match_profile: bool = True,
                 price_guard: bool = True, price_guard_share: float = 0.4,
                 max_paid: int = 2, max_repeat: int = 2
                 ) -> tuple[list[dict], int]:
    """Return ``([{offer, because}, ...], guard_swaps)`` for one reservation.

    Mirrors docs/how-it-works.md step 6-7 exactly, including the documented
    quirk that a guard-exempt repeat still counts toward ``max_paid``.
    """
    n = nights(res.check_in, res.check_out)
    cap = price_guard_share * nightly_rate(res)
    chosen: list[dict] = []
    guard_swaps = 0
    paid_count = 0

    if match_profile:
        for offer, why in repeat_offers(res, offers, cap=max_repeat):
            if paid_count >= max_paid:
                break
            chosen.append({"offer": offer, "because": why})
            if offer.price > 0:
                paid_count += 1

        keys = signal_keys(res)
        by_id = {o["offer"].id for o in chosen}
        candidates = []
        for offer in offers:
            if offer.id in by_id or offer.id in GENERIC_OFFER_IDS:
                continue
            hits = keys & set(offer.match_keys)
            if hits:
                candidates.append((len(hits), -len(offer.match_keys), offer, sorted(hits)[0]))
        candidates.sort(key=lambda c: (-c[0], c[1], -offer_value(c[2], n)))

        for _hits, _mk, offer, key in candidates:
            if paid_count >= max_paid:
                break
            value = offer_value(offer, n)
            if price_guard and offer.price > 0 and value > cap:
                guard_swaps += 1
                continue
            chosen.append({"offer": offer, "because": because_for(res, key)})
            if offer.price > 0:
                paid_count += 1

    if not chosen:
        for oid in GENERIC_OFFER_IDS:
            offer = next((o for o in offers if o.id == oid), None)
            if offer is not None:
                chosen.append({"offer": offer,
                               "because": "No preferences captured yet - the "
                                          "two universal offers, nothing invented"})
    return chosen, guard_swaps


# --------------------------------------------------------------------------
# money / rounding
# --------------------------------------------------------------------------
def round_to_5(value: float) -> int:
    return int(round(value / 5.0) * 5)


def money(amount: float, currency: str = "EUR") -> str:
    return f"{currency} {amount:,.0f}"
