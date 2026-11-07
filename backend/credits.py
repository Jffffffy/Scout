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
    "laundry": 30,  # upper bound; see laundry_stale_minutes()
}

BOUNTY_FRESH = 1
BOUNTY_STALE = 3
BOUNTY_WANTED = 5
DEMAND_WINDOW_MIN = 30

COST_DETAIL = 1
COST_ASK = 2

GPS_RADIUS_M = 80
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
