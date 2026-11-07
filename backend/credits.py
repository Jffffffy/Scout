"""Freshness rules and the bounty economy.

Rules
- Each place type goes stale at a different speed.
- Uploading earns a bounty. Stale places pay more, and stale places that
  someone recently asked about pay the most. This is how new credits enter
  the system, so the economy never runs dry.
- Viewing details costs 1 credit, asking Scout costs 2.
"""

import math
import time
from typing import Any, Dict, Optional

# minutes until data for a category counts as stale
STALE_MINUTES = {
    "dining": 10,
    "gym": 15,
    "office_hours": 15,
    "study": 30,
    "lounge": 30,
    "laundry": 30,  # upper bound; see laundry_stale_minutes()
}

# ---------------------------------------------------------------------------
# Busy levels
# ---------------------------------------------------------------------------
NOT_BUSY, MODERATE, BUSY = "not_busy", "moderate", "busy"
SEVERITY = [NOT_BUSY, MODERATE, BUSY]
LOW_RATE, HIGH_RATE = 0.40, 0.70          # occupancy thresholds
LINE_LOW, LINE_HIGH = 3, 10               # dining line thresholds (people)
MAX_REPORTS_PER_PLACE = 10


def level_from_rate(rate: float) -> str:
    if rate < LOW_RATE:
        return NOT_BUSY
    return MODERATE if rate <= HIGH_RATE else BUSY


def level_from_line(people: float) -> str:
    if people <= LINE_LOW:
        return NOT_BUSY
    return MODERATE if people <= LINE_HIGH else BUSY


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 else None


def _seats(data):
    """(occupied, total) seats from one report, or None if the AI could not count."""
    total, occ = _num(data.get("total_seats")), _num(data.get("occupied_seats"))
    if total and occ is not None:
        return min(occ, total), total
    return None


def _equipment(data):
    eq = [e for e in data.get("equipment") or [] if isinstance(e, dict)]
    if not eq:
        return None
    return sum(1 for e in eq if not e.get("free")), len(eq)


def _laundry(data):
    ms = [m for m in data.get("machines") or [] if isinstance(m, dict)]
    if not ms:
        return None
    return sum(1 for m in ms if m.get("status") != "free"), len(ms)


def summarize(category: str, reports: list) -> dict:
    """Combine all fresh reports of one place into one rating.

    Seats from several photos are ADDED before dividing, e.g.
    photo 1: 8/10 seats taken, photo 2: 2/10  ->  (8+2)/(10+10) = 50% -> moderate.
    """
    if not reports:
        return {}
    datas = [r["data"] for r in reports]
    out = {"n_reports": len(reports)}

    if category == "dining":
        lines = [_num(d.get("line_length")) for d in datas]
        lines = [x for x in lines if x is not None]
        if lines:
            avg = sum(lines) / len(lines)
            out.update(line_length=round(avg, 1), wait_minutes=round(avg * 40 / 60), level=level_from_line(avg))
    if category == "laundry":
        datas = datas[:1]  # machines change fast: only the newest photo counts
    counter = {"gym": _equipment, "laundry": _laundry}.get(category, _seats)
    pairs = [c for c in (counter(d) for d in datas) if c]
    if pairs:
        occ, tot = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
        out.update(occupied=occ, total=tot, occupancy=round(occ / tot, 2))
        seat_level = level_from_rate(occ / tot)
        # dining: use whichever is busier, the line or the seats
        out["level"] = max(out.get("level", NOT_BUSY), seat_level, key=SEVERITY.index)
    if "level" not in out:  # AI could not count anything: use its overall impression (majority vote)
        votes = [d.get("crowd") for d in datas if d.get("crowd") in (NOT_BUSY, MODERATE, BUSY)]
        if votes:
            out["level"] = max(set(votes), key=votes.count)
    return out


BOUNTY_FRESH = 1
BOUNTY_STALE = 3
BOUNTY_WANTED = 5
DEMAND_WINDOW_MIN = 30

COST_DETAIL = 1
COST_ASK = 2

GPS_RADIUS_M = 80

# location fixes: 2 different people must agree within this distance
FIX_AGREE_M = 40
FIX_REWARD = 2
FIX_MIN_MOVE_M = 15            # suggestions closer than this to the current pin are ignored
CAMPUS_BOX = (40.4330, 40.4530, -79.9600, -79.9300)  # lat_min, lat_max, lng_min, lng_max

# new places added by students
NEW_PLACE_REWARD = 2          # paid to the creator when someone else's photo there passes the AI check
DUPLICATE_RADIUS_M = 30
CATEGORIES = ("study", "dining", "lounge", "laundry", "gym")
REANALYZE_MINUTES = 10        # blurred copy kept this long so a report can be moved to the right place
UPLOAD_COOLDOWN_MIN = 10


def laundry_stale_minutes(data: Dict[str, Any]) -> float:
    """Laundry goes stale as soon as the next running machine should finish."""
    remaining = [
        m.get("minutes_left")
        for m in data.get("machines", [])
        if m.get("status") == "running" and isinstance(m.get("minutes_left"), (int, float))
    ]
    if remaining:
        return max(1.0, min(min(remaining), STALE_MINUTES["laundry"]))
    return STALE_MINUTES["laundry"]


def stale_after_minutes(category: str, data: Optional[Dict[str, Any]] = None) -> float:
    if category == "laundry" and data:
        return laundry_stale_minutes(data)
    return STALE_MINUTES.get(category, 30)


def minutes_since(ts: float, now: Optional[float] = None) -> float:
    return ((now or time.time()) - ts) / 60.0


def is_stale(place: Dict[str, Any], state: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
    if not state:
        return True
    limit = stale_after_minutes(place["category"], state["data"])
    return minutes_since(state["updated_at"], now) > limit


def bounty(place: Dict[str, Any], state: Optional[Dict[str, Any]], demand_ts: Optional[float],
           now: Optional[float] = None) -> int:
    now = now or time.time()
    if not is_stale(place, state, now):
        return BOUNTY_FRESH
    if demand_ts and minutes_since(demand_ts, now) <= DEMAND_WINDOW_MIN:
        return BOUNTY_WANTED
    return BOUNTY_STALE


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(meters: float) -> int:
    return max(1, round(meters / 80))  # ~80 m per minute walking
