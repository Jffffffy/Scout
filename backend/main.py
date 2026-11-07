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
from fastapi.responses import FileResponse, JSONResponse, Response
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
    """What everyone sees for free on the map: busy level, freshness and bounty."""
    now = now or time.time()
    fresh = store.fresh_reports(place["id"])
    state = store.get_state(place["id"])  # newest report, may be stale
    summary = C.summarize(place["category"], fresh)
    return {
        "id": place["id"],
        "name": place["name"],
        "category": place["category"],
        "description": place["description"],
        "lat": place["lat"],
        "lng": place["lng"],
        "level": summary.get("level"),          # not_busy | moderate | busy | None (stale / unknown)
        "n_reports": len(fresh),
        "has_report": state is not None,
        "fresh": bool(fresh),
        "minutes_old": None if not state else round(C.minutes_since(state["updated_at"], now)),
        "stale_after": round(C.stale_after_minutes(place["category"], state["data"] if state else None)),
        "bounty": C.bounty(place, state, store.demand.get(place["id"]), now),
        "room_note": place.get("room_note"),
        "pending_fixes": len(store.fixes.get(place["id"], [])),
        "corrected": bool(place.get("corrected")),
    }


def _report_view(place, report, now):
    single = C.summarize(place["category"], [report])
    d = report["data"]
    return {
        "id": report["id"],
        "minutes_old": round(C.minutes_since(report["updated_at"], now)),
        "summary": d.get("summary"),
        "level": single.get("level"),
        "occupied": single.get("occupied"),
        "total": single.get("total"),
        "line_length": d.get("line_length"),
        "has_photo": report.get("photo") is not None,
    }


UNLOCK_MINUTES = 30


def _can_see(user_id, place_id, report):
    if report.get("user_id") == user_id:
        return True
    t = store.unlocks.get((user_id, place_id))
    return t is not None and C.minutes_since(t) <= UNLOCK_MINUTES


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
    """Costs 1 credit (free again for 30 min after paying): the AI summary of the whole area,
    plus every fresh blurred photo it is based on."""
    place = store.places.get(place_id)
    if not place:
        raise HTTPException(404, "Unknown place")
    user = _user_or_404(user_id)
    now = time.time()
    fresh = store.fresh_reports(place_id)
    if not fresh:
        store.mark_demand(place_id)  # someone wants it -> bounty goes up
        return {**_public_place(place, now), "summary": None, "reports": [], "charged": 0, "credits": user["credits"]}

    charged = 0
    already = store.unlocks.get((user_id, place_id))
    if not (already and C.minutes_since(already, now) <= UNLOCK_MINUTES):
        if not store.spend(user_id, C.COST_DETAIL):
            return _payment_required(user, C.COST_DETAIL)
        store.unlocks[(user_id, place_id)] = now
        charged = C.COST_DETAIL

    newest = fresh[0]["data"]
    extras = {k: newest.get(k) for k in ("outlets_available", "whiteboard_free", "noise", "equipment", "machines")
              if newest.get(k) is not None}
    return {
        **_public_place(place, now),
        "summary": {**C.summarize(place["category"], fresh), **extras, "latest_summary": newest.get("summary")},
        "reports": [_report_view(place, r, now) for r in fresh],
        "charged": charged,
        "credits": user["credits"],
    }


@app.get("/api/reports/{report_id}/photo")
def report_photo(report_id: str, user_id: str):
    """The blurred + annotated photo (faces blurred, people covered, empty seats outlined).
    Visible to the uploader and to anyone who unlocked this place in the last 30 min."""
    _user_or_404(user_id)
    place_id, report = store.find_report(report_id)
    if not report or report.get("photo") is None:
        raise HTTPException(404, "This photo has expired.")
    if not _can_see(user_id, place_id, report):
        raise HTTPException(402, "Unlock this place (1 credit) to see its photos.")
    return Response(content=report["photo"], media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store"})


# ---------------------------------------------------------------------------
# crowd-sourced location fixes: 2 people agree within 40 m -> pin moves, both earn +2
# ---------------------------------------------------------------------------
class FixBody(BaseModel):
    user_id: str
    lat: float
    lng: float
    note: Optional[str] = None


@app.post("/api/places/{place_id}/fix")
def fix_location(place_id: str, body: FixBody):
    place = store.places.get(place_id)
    if not place:
        raise HTTPException(404, "Unknown place")
    user = _user_or_404(body.user_id)
    lat_min, lat_max, lng_min, lng_max = C.CAMPUS_BOX
    if not (lat_min <= body.lat <= lat_max and lng_min <= body.lng <= lng_max):
        raise HTTPException(400, "That spot is outside the CMU campus area.")
    note = (body.note or "").strip()[:80] or None
    pending = store.fixes.setdefault(place_id, [])
    if any(f["user_id"] == user["id"] for f in pending):
        raise HTTPException(400, "You already suggested a spot for this place. Waiting for someone to confirm it.")
    if C.distance_m(body.lat, body.lng, place["lat"], place["lng"]) < C.FIX_MIN_MOVE_M and not note:
        raise HTTPException(400, "That is basically where the pin already is.")

    mine = {"user_id": user["id"], "lat": body.lat, "lng": body.lng, "note": note, "ts": time.time()}
    match = next((f for f in pending
                  if f["user_id"] != user["id"]
                  and C.distance_m(f["lat"], f["lng"], body.lat, body.lng) <= C.FIX_AGREE_M), None)
    if not match:
        pending.append(mine)
        return {"status": "pending", "credits": user["credits"], "credits_earned": 0,
                "message": f"Thanks! When one more person suggests a spot within {C.FIX_AGREE_M} m of yours, "
                           f"the pin moves and you both get +{C.FIX_REWARD}."}

    # two people agree: move the pin to the midpoint and pay both
    old = (place["lat"], place["lng"])
    with store.lock:
        place["lat"] = round((match["lat"] + body.lat) / 2, 6)
        place["lng"] = round((match["lng"] + body.lng) / 2, 6)
        place["corrected"] = True
        place["room_note"] = note or match.get("note") or place.get("room_note")
        store.fixes[place_id] = []
    store.add_credits(match["user_id"], C.FIX_REWARD)
    store.add_credits(user["id"], C.FIX_REWARD)
    moved = round(C.distance_m(old[0], old[1], place["lat"], place["lng"]))
    return {"status": "moved", "credits": user["credits"], "credits_earned": C.FIX_REWARD,
            "lat": place["lat"], "lng": place["lng"], "moved_m": moved,
            "message": f"Confirmed by 2 people! {place['name']} moved {moved} m. You both earned +{C.FIX_REWARD}."}


@app.get("/api/places-export")
def export_places():
    """Current places (with crowd-corrected coordinates) in the same format as places.json,
    so fixes can be saved permanently into the repo."""
    keys = ("id", "name", "category", "lat", "lng", "description", "room_note")
    return [{k: p[k] for k in keys if p.get(k) is not None} for p in store.places.values()]


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
        by_dist = sorted(store.places.values(), key=lambda p: C.distance_m(lat, lng, p["lat"], p["lng"]))
        nearest = by_dist[0]
        if place is None:
            # GPS cannot tell rooms in the same building apart: if several places are close, let the user pick.
            nearby = [p for p in by_dist if C.distance_m(lat, lng, p["lat"], p["lng"]) <= C.GPS_RADIUS_M]
            if len(nearby) > 1:
                names = ", ".join(p["name"] for p in nearby[:6])
                return JSONResponse(status_code=200, content={
                    "accepted": False, "needs_choice": True, "credits_earned": 0, "credits": user["credits"],
                    "photo_stored": False,
                    "message": f"Several places are near you ({names}). Pick one and upload again.",
                    "options": [{"id": p["id"], "name": p["name"], "category": p["category"]} for p in nearby[:8]],
                })
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

    # 4. AI: is it valid, what is the state, where are people and empty seats?
    try:
        analysis = await asyncio.to_thread(vision.analyze_photo, clean, place)
    except Exception as e:  # network / model error
        del clean
        raise HTTPException(502, f"AI check failed: {e}")

    if not analysis.get("valid"):
        del clean
        return _reject(user, f"Photo not accepted: {analysis.get('reject_reason') or 'not a clear photo of this place'}.",
                       analysis=analysis)

    # 5. second privacy layer: cover every person with a gray block, outline empty seats.
    #    Only this annotated version is kept, and only until the place goes stale (max 30 min).
    people, empty = analysis.pop("people", []), analysis.pop("empty_seats", [])
    try:
        shared_photo = await asyncio.to_thread(privacy.annotate, clean, people, empty)
    except Exception:
        shared_photo = None
    del clean  # the blurred working copy is gone

    # 6. update the place and pay the bounty
    analysis["people_covered"] = len(people)
    report = store.add_report(place["id"], analysis, shared_photo, user["id"])
    store.demand.pop(place["id"], None)
    earned = 0 if on_cooldown else reward
    if earned:
        store.award(user["id"], place["id"], earned)

    if on_cooldown:
        msg = f"Thanks! {place['name']} updated. No credits: you already earned here in the last {C.UPLOAD_COOLDOWN_MIN} min."
    else:
        msg = f"+{earned} credits for {place['name']}! Balance: {user['credits']}."

    now = time.time()
    return {
        "accepted": True,
        "message": msg,  # the iPhone Shortcut shows this as a notification
        "place": place["name"],
        "place_id": place["id"],
        "credits_earned": earned,
        "credits": user["credits"],
        "faces_blurred": faces,
        "people_covered": len(people),
        "photo_stored": False,  # the original is never stored
        "report": _report_view(place, report, now),
        "photo_url": f"/api/reports/{report['id']}/photo" if shared_photo else None,
        "area": _public_place(place, now),
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
        fresh = store.fresh_reports(p["id"])
        summary = C.summarize(p["category"], fresh)
        newest = state["data"] if state else None
        candidates.append({
            "id": p["id"], "name": p["name"], "category": p["category"], "description": p["description"],
            "walk_minutes": walk,
            "fresh": bool(fresh),
            "minutes_old": None if not state else round(C.minutes_since(state["updated_at"], now)),
            "level": summary.get("level"),
            "occupancy": summary.get("occupancy"),
            "n_reports": len(fresh),
            "state": None if not newest else {k: v for k, v in newest.items() if k not in ("model", "mock")},
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
            "levels": {"not_busy": f"< {int(C.LOW_RATE*100)}% seats taken", "moderate": f"{int(C.LOW_RATE*100)}-{int(C.HIGH_RATE*100)}%",
                       "busy": f"> {int(C.HIGH_RATE*100)}%", "dining_line": f"<= {C.LINE_LOW} / <= {C.LINE_HIGH} / more people"},
            "photo_kept_minutes": "until the place goes stale (max 30)",
        },
        "places": len(store.places),
    }


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
