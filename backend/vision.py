"""All LLM calls go through OpenRouter.

analyze_photo(): blurred photo -> validity check + structured state
ask_scout():     natural-language need + all place states -> top 3 picks

If OPENROUTER_KEY is not set, both functions fall back to a mock so the app
still runs end to end (handy for judges cloning the repo).
"""

import base64
import json
import os
import random
import re
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = os.getenv("VISION_MODEL", "google/gemini-omni-1.2-flash")
ASK_MODEL = os.getenv("ASK_MODEL", VISION_MODEL)
# Used automatically if the main model errors, times out or returns nothing.
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "google/gemini-2.5-flash")


def _key() -> str:
    return os.getenv("OPENROUTER_KEY", "").strip()


def mock_mode() -> bool:
    return not _key()


# ---------- schemas the vision model must fill, per place category ----------
CATEGORY_FIELDS = {
    "study": {
        "free_seats": "integer, empty seats you can see",
        "outlets_available": "boolean, a free power outlet is visible near an empty seat",
        "whiteboard_free": "boolean or null if no whiteboard is visible",
        "noise": "one of quiet | moderate | loud (judge from how many people are talking or grouped)",
    },
    "dining": {
        "line_length": "integer, people waiting in line",
        "wait_minutes": "integer, rough estimate assuming ~40 seconds per person",
        "seating_available": "boolean",
    },
    "gym": {
        "equipment": "list of {name: string, free: boolean} for each machine/rack you can see",
    },
    "laundry": {
        "machines": "list of {id: string label if visible else position like 'left-1', type: washer|dryer, "
                    "status: free | running | done_clothes_inside, minutes_left: integer or null}. "
                    "Read the time on each display if visible. A stopped machine with clothes visible "
                    "through the glass door is done_clothes_inside.",
    },
    "office_hours": {
        "queue_length": "integer, students waiting",
        "tas_visible": "integer",
    },
}


def _call(model: str, messages: List[Dict[str, Any]], max_tokens: int, timeout: int) -> str:
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1},
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise ValueError(f"{model} returned an empty reply")
    return content


def _post(model: str, messages: List[Dict[str, Any]], max_tokens: int = 4000) -> Tuple[str, str]:
    """Try the main (smartest) model first; fall back to a fast model if it fails.
    Returns (reply text, model actually used). Reasoning models spend tokens on thinking,
    so max_tokens is generous."""
    try:
        return _call(model, messages, max_tokens, timeout=45), model
    except Exception as e:
        if not FALLBACK_MODEL or FALLBACK_MODEL == model:
            raise
        print(f"[vision] {model} failed ({e}); falling back to {FALLBACK_MODEL}")
        return _call(FALLBACK_MODEL, messages, max_tokens, timeout=40), FALLBACK_MODEL


def _parse_json(text: str) -> Dict[str, Any]:
    """Models sometimes wrap JSON in ```json fences or add a sentence. Grab the object."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in model reply: {text[:200]}")
    return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# 1. Photo analysis
# ---------------------------------------------------------------------------
def analyze_photo(jpeg_bytes: bytes, place: Dict[str, Any]) -> Dict[str, Any]:
    if mock_mode():
        return _mock_analysis(place)

    fields = CATEGORY_FIELDS.get(place["category"], {})
    field_lines = "\n".join(f'  "{k}": {v}' for k, v in fields.items())
    prompt = f"""You verify crowd-sourced photos for Scout, a campus app that shows students
which spaces are free right now. The uploader claims this photo was just taken at:

Place: {place['name']}
Type: {place['category']}
About: {place['description']}

Step 1. Decide if the photo is a VALID live photo of this kind of place.
Reject it if it is: a screenshot or photo of a screen, a photo of a printed picture,
a meme or stock image, a selfie or close-up with no view of the space, too dark or
blurry to judge, or clearly a different kind of place (for example a bedroom when the
type is dining).

Step 2. If valid, describe the current state using ONLY what you can see.

Reply with ONE JSON object and nothing else:
{{
  "valid": boolean,
  "reject_reason": string or null,
  "availability": "high" | "medium" | "low",   // how easy it is to use this place right now
  "summary": string,                           // max 15 words, for students, no people descriptions
{field_lines}
}}
Never describe what any person looks like."""

    b64 = base64.b64encode(jpeg_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }]
    reply, used = _post(VISION_MODEL, messages)
    result = _parse_json(reply)
    result["model"] = used
    result["valid"] = bool(result.get("valid"))
    if result.get("availability") not in ("high", "medium", "low"):
        result["availability"] = "medium"
    return result


def _mock_analysis(place: Dict[str, Any]) -> Dict[str, Any]:
    cat = place["category"]
    avail = random.choice(["high", "medium", "low"])
    base = {"valid": True, "reject_reason": None, "availability": avail, "mock": True}
    if cat == "study":
        base.update(free_seats=random.randint(0, 20), outlets_available=random.random() > 0.4,
                    whiteboard_free=random.random() > 0.5, noise=random.choice(["quiet", "moderate", "loud"]),
                    summary="Several open tables, a few near outlets.")
    elif cat == "dining":
        n = random.randint(0, 15)
        base.update(line_length=n, wait_minutes=round(n * 40 / 60), seating_available=True,
                    summary=f"About {n} people in line.")
    elif cat == "gym":
        base.update(equipment=[{"name": "squat rack", "free": random.random() > 0.5},
                               {"name": "treadmill", "free": True}],
                    summary="Treadmills open, racks busy.")
    elif cat == "laundry":
        base.update(machines=[
            {"id": "W1", "type": "washer", "status": "free", "minutes_left": None},
            {"id": "W2", "type": "washer", "status": "running", "minutes_left": random.randint(5, 30)},
            {"id": "D1", "type": "dryer", "status": "done_clothes_inside", "minutes_left": None},
        ], summary="1 washer free, 1 dryer done with clothes inside.")
    return base


# ---------------------------------------------------------------------------
# 2. Ask Scout
# ---------------------------------------------------------------------------
def ask_scout(query: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """candidates: [{id, name, category, description, walk_minutes, fresh, minutes_old, state}]"""
    if mock_mode():
        return _mock_ask(query, candidates)

    prompt = f"""You are Scout, a campus space finder at Carnegie Mellon.
A student asks: "{query}"

Live data for each place (state is null if nobody has reported it yet; fresh=false means the
report is old and may be wrong):
{json.dumps(candidates, indent=1)}

Pick up to 3 places that best fit the request. Prefer fresh data that clearly matches.
Use a stale place only if nothing fresh fits, and say it may be out of date.
If walk_minutes is present, respect any distance or time limit in the request.

Reply with ONE JSON object and nothing else:
{{
  "recommendations": [{{"place_id": string, "reason": string (max 25 words, cite the live data)}}],
  "note": string or null   // e.g. what info is missing
}}"""
    reply, used = _post(ASK_MODEL, [{"role": "user", "content": prompt}])
    result = _parse_json(reply)
    valid_ids = {c["id"] for c in candidates}
    recs = [r for r in result.get("recommendations", []) if r.get("place_id") in valid_ids][:3]
    return {"recommendations": recs, "note": result.get("note"), "model": used}


def _mock_ask(query: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    q = query.lower()
    wants = {
        "study": any(w in q for w in ["study", "quiet", "outlet", "whiteboard", "seat", "work", "学习", "自习"]),
        "dining": any(w in q for w in ["eat", "food", "lunch", "dinner", "hungry", "饭", "吃"]),
        "gym": any(w in q for w in ["gym", "workout", "squat", "run", "健身"]),
        "laundry": any(w in q for w in ["laundry", "wash", "dryer", "洗衣"]),
    }
    score = {"high": 3, "medium": 2, "low": 1}

    def rank(c):
        s = score.get((c.get("state") or {}).get("availability"), 0)
        s += 2 if c.get("fresh") else 0
        s += 3 if wants.get(c["category"]) else 0
        s -= (c.get("walk_minutes") or 0) / 10
        return s

    top = sorted(candidates, key=rank, reverse=True)[:3]
    return {
        "recommendations": [
            {"place_id": c["id"], "reason": ((c.get("state") or {}).get("summary") or "No recent report yet.")}
            for c in top
        ],
        "note": "Mock mode: add OPENROUTER_KEY to .env for real AI answers.",
    }
