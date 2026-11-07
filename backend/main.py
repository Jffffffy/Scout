"""Scout: the Waze for campus spaces.

Run from the repo root:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import time
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import credits as C
from . import privacy, vision
from .store import store

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

app = FastAPI(title="Scout")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _user_or_404(user_id: str):
    user = store.users.get(user_id)
    if not user:
        raise HTTPException(404, "Unknown user. Create an account first.")
    return user


def _public_place(place, now=None):
    """What everyone sees for free on the map: color + freshness + bounty, no details."""
    now = now or time.time()
    state = store.get_state(place["id"])
    stale = C.is_stale(place, state, now)
    return {
        "id": place["id"],
        "name": place["name"],
        "category": place["category"],
        "lat": place["lat"],
        "lng": place["lng"],
        "availability": None if (stale or not state) else state["data"].get("availability"),
        "has_report": state is not None,
        "fresh": not stale,
        "minutes_old": None if not state else round(C.minutes_since(state["updated_at"], now)),
        "stale_after": round(C.stale_after_minutes(place["category"], state["data"] if state else None)),
        "bounty": C.bounty(place, state, store.demand.get(place["id"]), now),
    }


def _best_bounty():
    places = [_public_place(p) for p in store.places.values()]
    return max(places, key=lambda p: p["bounty"]) if places else None


def _payment_required(user, cost):
    best = _best_bounty()
    hint = f" Snap {best['name']} to earn {best['bounty']}." if best else ""
    return JSONResponse(
        status_code=402,
        content={"detail": f"Not enough credits (need {cost}, have {user['credits']}).{hint}",
                 "credits": user["credits"]},
    )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class Credentials(BaseModel):
    username: str
    password: str


@app.post("/api/signup")
def signup(body: Credentials):
    try:
        return store.public(store.signup(body.username, body.password))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/login")
def login(body: Credentials):
    user = store.login(body.username, body.password)
    if not user:
        raise HTTPException(401, "Wrong username or password.")
    return store.public(user)


@app.get("/api/users/{user_id}")
def get_user(user_id: str):
    return store.public(_user_or_404(user_id))


@app.get("/api/leaderboard")
def leaderboard():
    return store.leaderboard()


# ---------------------------------------------------------------------------
# places
# ---------------------------------------------------------------------------
@app.get("/api/places")
def list_places():
    """Free: the map only shows colors, freshness and bounties."""
    now = time.time()
    return [_public_place(p, now) for p in store.places.values()]


@app.get("/api/places/{place_id}")
def place_detail(place_id: str, user_id: str):
    """Costs 1 credit: full AI-extracted state of one place."""
    place = store.places.get(place_id)
    if not place:
        raise HTTPException(404, "Unknown place")
    user = _user_or_404(user_id)
    state = store.get_state(place_id)
    if not state:
        store.mark_demand(place_id)  # someone wants it -> bounty goes up
        return {**_public_place(place), "state": None, "charged": 0, "credits": user["credits"]}
    if not store.spend(user_id, C.COST_DETAIL):
        return _payment_required(user, C.COST_DETAIL)
    if C.is_stale(place, state):
        store.mark_demand(place_id)
    return {**_public_place(place), "state": state["data"], "charged": C.COST_DETAIL, "credits": user["credits"]}


# ---------------------------------------------------------------------------
# upload: blur -> AI check -> pay bounty -> forget the photo
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(
    photo: UploadFile = File(...),
    user_id: str = Form(...),
    lat: Optional[float] = Form(None),
    lng: Optional[float] = Form(None),
    place_id: Optional[str] = Form(None),
):
    user = _user_or_404(user_id.strip())
    raw = await photo.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Photo too large (max 12 MB).")

    # 1. which place is this? GPS must match (skipped in demo mode)
    place = None
    if place_id and place_id in store.places:
        place = store.places[place_id]
    if lat is not None and lng is not None:
        nearest = min(store.places.values(), key=lambda p: C.distance_m(lat, lng, p["lat"], p["lng"]))
        if place is None:
            place = nearest
        dist = C.distance_m(lat, lng, place["lat"], place["lng"])  # distance to the place actually used
        if not DEMO_MODE and dist > C.GPS_RADIUS_M:
            return _reject(user, f"You are {round(dist)} m from {place['name']}. "
                                 f"Photos must be taken within {C.GPS_RADIUS_M} m.")
    elif not DEMO_MODE:
        return _reject(user, "Location is required so we know the photo is real.")
    if place is None:
        return _reject(user, "Pick a place for this photo.")

    # 2. bounty is priced BEFORE this upload refreshes the place
    now = time.time()
    state_before = store.get_state(place["id"])
    reward = C.bounty(place, state_before, store.demand.get(place["id"]), now)
    last = store.last_award.get((user["id"], place["id"]))
    on_cooldown = last is not None and C.minutes_since(last, now) < C.UPLOAD_COOLDOWN_MIN

    # 3. privacy: blur faces + strip EXIF BEFORE the photo goes to any AI model
    try:
        # run in a worker thread so other users are not blocked while we process
        clean, faces = await asyncio.to_thread(privacy.blur_faces, raw)
    except privacy.PrivacyError as e:
        del raw
        return _reject(user, str(e))
    del raw  # original photo is gone

    # 4. AI: is it valid, and what is the state?
    try:
        analysis = await asyncio.to_thread(vision.analyze_photo, clean, place)
    except Exception as e:  # network / model error
        del clean
        raise HTTPException(502, f"AI check failed: {e}")
    del clean  # blurred photo is gone too. We keep only the JSON below.

    if not analysis.get("valid"):
        return _reject(user, f"Photo not accepted: {analysis.get('reject_reason') or 'not a clear photo of this place'}.",
                       analysis=analysis)

    # 5. update the place and pay the bounty
    store.set_state(place["id"], analysis)
    store.demand.pop(place["id"], None)
    earned = 0 if on_cooldown else reward
    if earned:
        store.award(user["id"], place["id"], earned)

    if on_cooldown:
        msg = f"Thanks! {place['name']} updated. No credits: you already earned here in the last {C.UPLOAD_COOLDOWN_MIN} min."
    else:
        msg = f"+{earned} credits for {place['name']}! Balance: {user['credits']}."

    return {
        "accepted": True,
        "message": msg,  # the iPhone Shortcut shows this as a notification
        "place": place["name"],
        "credits_earned": earned,
        "credits": user["credits"],
        "faces_blurred": faces,
        "photo_stored": False,
        "summary": analysis.get("summary"),
        "analysis": analysis,  # the full JSON the AI extracted (shown in the UI for testing)
        "mock": analysis.get("mock", False),
    }


def _reject(user, reason, analysis=None):
    return JSONResponse(status_code=200, content={
        "accepted": False, "message": reason, "credits_earned": 0,
        "credits": user["credits"], "photo_stored": False, "analysis": analysis,
    })


# ---------------------------------------------------------------------------
# ask scout: natural language -> top 3 places (costs 2 credits)
# ---------------------------------------------------------------------------
class AskBody(BaseModel):
    user_id: str
    query: str
    lat: Optional[float] = None
    lng: Optional[float] = None


@app.post("/api/ask")
def ask(body: AskBody):
    user = _user_or_404(body.user_id)
    if not body.query.strip():
        raise HTTPException(400, "Ask something first.")
    if not store.spend(user["id"], C.COST_ASK):
        return _payment_required(user, C.COST_ASK)

    now = time.time()
    candidates = []
    for p in store.places.values():
        state = store.get_state(p["id"])
        walk = None
        if body.lat is not None and body.lng is not None:
            walk = C.walk_minutes(C.distance_m(body.lat, body.lng, p["lat"], p["lng"]))
        candidates.append({
            "id": p["id"], "name": p["name"], "category": p["category"], "description": p["description"],
            "walk_minutes": walk,
            "fresh": not C.is_stale(p, state, now),
            "minutes_old": None if not state else round(C.minutes_since(state["updated_at"], now)),
            "state": state["data"] if state else None,
        })

    try:
        result = vision.ask_scout(body.query.strip()[:300], candidates)
    except Exception as e:
        with store.lock:
            user["credits"] += C.COST_ASK  # refund on AI failure
        raise HTTPException(502, f"Scout could not answer: {e}")

    by_id = {c["id"]: c for c in candidates}
    recs = []
    for r in result["recommendations"]:
        c = by_id[r["place_id"]]
        if not c["fresh"]:
            store.mark_demand(c["id"])  # stale pick -> raise its bounty
        recs.append({**_public_place(store.places[c["id"]], now), "reason": r.get("reason", "")})
    return {"recommendations": recs, "note": result.get("note"), "credits": user["credits"], "charged": C.COST_ASK}


# ---------------------------------------------------------------------------
# meta + frontend
# ---------------------------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "demo_mode": DEMO_MODE,
        "mock_ai": vision.mock_mode(),
        "face_blur_ready": privacy.detector_ready(),
        "model": vision.VISION_MODEL,
        "ask_model": vision.ASK_MODEL,
        "fallback_model": vision.FALLBACK_MODEL,
        "rules": {
            "starter_credits": 5, "cost_detail": C.COST_DETAIL, "cost_ask": C.COST_ASK,
            "bounty": {"fresh": C.BOUNTY_FRESH, "stale": C.BOUNTY_STALE, "wanted": C.BOUNTY_WANTED},
            "stale_minutes": C.STALE_MINUTES, "gps_radius_m": C.GPS_RADIUS_M,
            "cooldown_min": C.UPLOAD_COOLDOWN_MIN,
        },
    }


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
