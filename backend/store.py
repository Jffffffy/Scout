"""In-memory data store.

Scout never stores photos. The only things kept are:
- the structured state the AI extracted for each place
- user credit balances and upload counts
- small timestamps used for anti-abuse and bounty pricing
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

PLACES_FILE = os.path.join(os.path.dirname(__file__), "places.json")

STARTER_CREDITS = 5
USERNAME_RE = re.compile(r"[A-Za-z0-9_-]{3,20}")


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        with open(PLACES_FILE) as f:
            self.places: Dict[str, Dict[str, Any]] = {p["id"]: p for p in json.load(f)}
        # place_id -> {"data": {...}, "updated_at": ts}
        self.states: Dict[str, Dict[str, Any]] = {}
        # user_id -> {"id", "name", "credits", "uploads", "created_at", "_salt", "_pw"}
        # The id is a secret random token. Passwords are stored only as salted PBKDF2 hashes.
        self.users: Dict[str, Dict[str, Any]] = {}
        # lowercase username -> user_id
        self.by_name: Dict[str, str] = {}
        # (user_id, place_id) -> last time this user was paid for this place
        self.last_award: Dict[tuple, float] = {}
        # place_id -> last time someone wanted info about it
        self.demand: Dict[str, float] = {}

    # ---------- users ----------
    @staticmethod
    def _hash(password: str, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()

    def signup(self, username: str, password: str) -> Dict[str, Any]:
        """Returns the user, or raises ValueError with a message for the UI."""
        name = (username or "").strip()
        if not USERNAME_RE.fullmatch(name):
            raise ValueError("Username: 3-20 letters, numbers, _ or -.")
        if len(password or "") < 4:
            raise ValueError("Password must be at least 4 characters.")
        with self.lock:
            if name.lower() in self.by_name:
                raise ValueError("That username is taken.")
            salt = os.urandom(16)
            user = {
                "id": secrets.token_urlsafe(9),  # secret: works as the login token and Shortcut key
                "name": name,
                "credits": STARTER_CREDITS,
                "uploads": 0,
                "created_at": time.time(),
                "_salt": salt.hex(),
                "_pw": self._hash(password, salt),
            }
            self.users[user["id"]] = user
            self.by_name[name.lower()] = user["id"]
            return user

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        uid = self.by_name.get((username or "").strip().lower())
        if not uid:
            return None
        user = self.users[uid]
        expected = self._hash(password or "", bytes.fromhex(user["_salt"]))
        return user if hmac.compare_digest(expected, user["_pw"]) else None

    @staticmethod
    def public(user: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in user.items() if not k.startswith("_")}

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
