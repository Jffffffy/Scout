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
SEATS_FIELDS = {
    "total_seats": "integer, ALL seats you can see (chairs, stools, sofa spots), taken or empty",
    "occupied_seats": "integer, seats with a person OR clearly reserved by belongings (bag, laptop, coat)",
}
CATEGORY_FIELDS = {
    "study": {
        **SEATS_FIELDS,
        "outlets_available": "boolean, a free power outlet is visible near an empty seat",
        "whiteboard_free": "boolean or null if no whiteboard is visible",
        "noise": "one of quiet | moderate | loud (judge from how many people are talking or grouped)",
    },
    "lounge": {
        **SEATS_FIELDS,
        "noise": "one of quiet | moderate | loud",
    },
    "dining": {
        "line_length": "integer, people waiting in line (0 if no line)",
        **SEATS_FIELDS,
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


def _post_json(model: str, messages: List[Dict[str, Any]], max_tokens: int = 4000) -> Tuple[Dict[str, Any], str]:
    """Ask the main model for JSON. If the call fails, times out, returns nothing,
    or returns text that is not valid JSON, retry once with the fallback model.
    Returns (parsed JSON, model actually used)."""
    try:
        return _parse_json(_call(model, messages, max_tokens, timeout=45)), model
    except Exception as e:
        if not FALLBACK_MODEL or FALLBACK_MODEL == model:
            raise
        print(f"[vision] {model} failed ({e}); falling back to {FALLBACK_MODEL}")
        return _parse_json(_call(FALLBACK_MODEL, messages, max_tokens, timeout=40)), FALLBACK_MODEL


def _as_bool(value: Any) -> bool:
    """Strict: only real true or the string "true" count. "false", "no", null, etc. are False."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


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

Step 3. Give bounding boxes as [ymin, xmin, ymax, xmax] on a 0-1000 scale
(0,0 = top-left of the image, 1000,1000 = bottom-right):
- "people": one box per visible person, covering the WHOLE body (head to feet).
- "empty_seats": one box per empty, unreserved seat. Box ONLY the chair or stool itself
  (seat and backrest), never the table or desk. Boxes must not overlap each other.

Reply with ONE JSON object and nothing else:
{{
  "valid": boolean,
  "reject_reason": string or null,
  "crowd": "not_busy" | "moderate" | "busy",   // your overall impression of this photo
  "summary": string,                          // max 15 words, for students, no people descriptions
{field_lines}
  "people": [[ymin, xmin, ymax, xmax], ...],
  "empty_seats": [[ymin, xmin, ymax, xmax], ...]
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
    result, used = _post_json(VISION_MODEL, messages)
    result["model"] = used
    result["valid"] = _as_bool(result.get("valid"))
    if result.get("crowd") not in ("not_busy", "moderate", "busy"):
        result["crowd"] = None
    for k in ("people", "empty_seats"):
        if not isinstance(result.get(k), list):
            result[k] = []
    return result


def _mock_boxes(n_people: int, n_empty: int):
    """Fake boxes laid out in a grid so the annotated photo can be tested without a key."""
    boxes, i = [], 0
    for _ in range(n_people + n_empty):
        r, c = divmod(i, 5)
        boxes.append([300 + r * 220, 60 + c * 190, 480 + r * 220, 220 + c * 190])
        i += 1
    return boxes[:n_people], boxes[n_people:]


def _mock_analysis(place: Dict[str, Any]) -> Dict[str, Any]:
    cat = place["category"]
    base = {"valid": True, "reject_reason": None, "crowd": random.choice(["not_busy", "moderate", "busy"]),
            "mock": True, "people": [], "empty_seats": []}
    if cat in ("study", "lounge", "dining"):
        total = random.randint(6, 10)
        occ = random.randint(0, total)
        base.update(total_seats=total, occupied_seats=occ)
        base["people"], base["empty_seats"] = _mock_boxes(occ, total - occ)
    if cat in ("study", "lounge"):
        base.update(outlets_available=random.random() > 0.4, whiteboard_free=random.random() > 0.5,
                    noise=random.choice(["quiet", "moderate", "loud"]),
                    summary=f"{base['total_seats'] - base['occupied_seats']} of {base['total_seats']} seats free.")
    elif cat == "dining":
        n = random.randint(0, 15)
        base.update(line_length=n, summary=f"About {n} people in line.")
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

Live data for each place. "level" is not_busy / moderate / busy, computed from all fresh photos
("n_reports" photos, "occupancy" = share of seats taken). state is null if nobody has reported it yet;
fresh=false means the report is old and may be wrong:
{json.dumps(candidates, indent=1)}

Pick up to 3 places that best fit the request. Prefer fresh data that clearly matches.
Use a stale place only if nothing fresh fits, and say it may be out of date.
If walk_minutes is present, respect any distance or time limit in the request.

Reply with ONE JSON object and nothing else:
{{
  "recommendations": [{{"place_id": string, "reason": string (max 25 words, cite the live data)}}],
  "note": string or null   // e.g. what info is missing
}}"""
    result, used = _post_json(ASK_MODEL, [{"role": "user", "content": prompt}])
    valid_ids = {c["id"] for c in candidates}
    recs = [r for r in result.get("recommendations", []) if r.get("place_id") in valid_ids][:3]
    return {"recommendations": recs, "note": result.get("note"), "model": used}


def _mock_ask(query: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    q = query.lower()
    wants = {
        "study": any(w in q for w in ["study", "quiet", "outlet", "whiteboard", "seat", "work", "学习", "自习"]),
        "lounge": any(w in q for w in ["lounge", "dorm", "hang", "chill", "宿舍"]),
        "dining": any(w in q for w in ["eat", "food", "lunch", "dinner", "hungry", "饭", "吃"]),
        "gym": any(w in q for w in ["gym", "workout", "squat", "run", "健身"]),
        "laundry": any(w in q for w in ["laundry", "wash", "dryer", "洗衣"]),
    }
    score = {"not_busy": 3, "moderate": 2, "busy": 1}

    def rank(c):
        s = score.get(c.get("level"), 0)
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
