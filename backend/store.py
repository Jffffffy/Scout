"""In-memory data store.

Scout never stores photos. The only things kept are:
- the structured state the AI extracted for each place
- user credit balances and upload counts
- small timestamps used for anti-abuse and bounty pricing
"""

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

PLACES_FILE = os.path.join(os.path.dirname(__file__), "places.json")

STARTER_CREDITS = 5


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        with open(PLACES_FILE) as f:
            self.places: Dict[str, Dict[str, Any]] = {p["id"]: p for p in json.load(f)}
        # place_id -> {"data": {...}, "updated_at": ts}
        self.states: Dict[str, Dict[str, Any]] = {}
        # user_id -> {"id", "name", "credits", "uploads", "created_at"}
        self.users: Dict[str, Dict[str, Any]] = {}
        # (user_id, place_id) -> last time this user was paid for this place
        self.last_award: Dict[tuple, float] = {}
        # place_id -> last time someone wanted info about it
        self.demand: Dict[str, float] = {}

    # ---------- users ----------
    def create_user(self, name: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            uid = user_id or uuid.uuid4().hex[:8]
            if uid in self.users:
                return self.users[uid]
            user = {
                "id": uid,
                "name": (name or "Scout").strip()[:24] or "Scout",
                "credits": STARTER_CREDITS,
                "uploads": 0,
                "created_at": time.time(),
            }
            self.users[uid] = user
            return user

    def get_or_create_user(self, user_id: str) -> Dict[str, Any]:
        """Used by the iPhone Shortcut: the first upload creates the account."""
        user = self.users.get(user_id)
        if user:
            return user
        return self.create_user(name=user_id, user_id=user_id)

    def spend(self, user_id: str, amount: int) -> bool:
        with self.lock:
            user = self.users.get(user_id)
            if not user or user["credits"] < amount:
                return False
            user["credits"] -= amount
            return True

    def award(self, user_id: str, place_id: str, amount: int) -> None:
        with self.lock:
            user = self.users[user_id]
            user["credits"] += amount
            user["uploads"] += 1
            self.last_award[(user_id, place_id)] = time.time()

    def leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        users = sorted(self.users.values(), key=lambda u: u["uploads"], reverse=True)
        return [{"name": u["name"], "uploads": u["uploads"]} for u in users[:limit] if u["uploads"] > 0]

    # ---------- places ----------
    def set_state(self, place_id: str, data: Dict[str, Any]) -> None:
        with self.lock:
            self.states[place_id] = {"data": data, "updated_at": time.time()}

    def get_state(self, place_id: str) -> Optional[Dict[str, Any]]:
        return self.states.get(place_id)

    def mark_demand(self, place_id: str) -> None:
        self.demand[place_id] = time.time()


store = Store()
