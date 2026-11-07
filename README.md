# 🧭 Scout: the Waze for campus spaces

**Tell Scout what you need. It tells you where to go right now.**

Students waste time walking to a full library floor, a long lunch line, or a laundry room with no free machine. Scout turns quick student photos into live, structured information about campus spaces, and pays people in credits for keeping it fresh.

> *Information is the currency. Stale places pay more.*

Built at **ScottyLabs Nova 2026** (CMU).

---

## How it works

```
Student snaps a photo (iPhone Shortcut or web)
   ⟹ server blurs faces and strips metadata
   ⟹ vision model checks the photo is real and extracts the state
        (free seats, outlets, whiteboard, line length, free machines, ...)
   ⟹ photo is deleted, only the JSON is kept
   ⟹ map updates, uploader earns a bounty
Another student asks: "quiet spot with an outlet near Tepper"
   ⟹ LLM ranks places using live state + walking time ⟹ top 3 with reasons
```

### The credit economy

| Action | Credits |
|---|---|
| New account | +5 |
| Photo of a place that was just updated | +1 |
| Photo of a **stale** place | +3 |
| Photo of a stale place **someone just asked about** | +5 |
| See full details of a place | −1 |
| Ask Scout | −2 |

Bounties are how new credits enter the system: the less fresh a place is, and the more people want it, the more a photo pays. This stops free riders and sends scouts where information is missing.

### Freshness

| Place type | Goes stale after |
|---|---|
| Dining | 10 min |
| Gym, office hours | 15 min |
| Study spaces | 30 min |
| Laundry | when the next running machine should finish (max 30 min) |

### Anti-abuse

- GPS must be within **80 m** of the place (turned off when `DEMO_MODE=true`)
- One paid upload per person per place every **10 min**
- The vision model rejects screenshots, photos of screens, stock images, selfies and wrong places

### Privacy by design

1. Faces are blurred **before** the photo is sent to any AI model. If the face detector is unavailable, the upload is refused.
2. Re-encoding removes EXIF metadata (GPS, device info).
3. The photo only lives in memory during the request and is never written to disk. Only the extracted JSON is kept.
4. The model is told never to describe people.

---

## Run it

Requires Python 3.10+.

```bash
git clone https://github.com/Jffffffy/nova_2026_scout.git
cd nova_2026_scout
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env          # then paste your OpenRouter key into .env
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000.

No key? It still runs in **mock AI mode**, so you can click through every feature.

Phone uploads: see [`shortcut/SHORTCUT.md`](shortcut/SHORTCUT.md).

## Deploy (public HTTPS link)

**Render (stable link, laptop can be off):** New + ⟹ Blueprint ⟹ pick this repo ⟹ it reads `render.yaml` ⟹ paste `OPENROUTER_KEY` when asked ⟹ Deploy. You get `https://scout-xxxx.onrender.com`.
Free instances sleep after ~15 min idle (first visit then takes ~50 s) and memory resets on restart.

**Quick tunnel (2 minutes, laptop must stay on):**
```bash
cloudflared tunnel --url http://localhost:8000
```

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/api/signup` | JSON `username`, `password` ⟹ account (+5 credits) |
| POST | `/api/login` | JSON `username`, `password` ⟹ account |
| GET | `/api/places` | Map data: color, freshness, bounty (free) |
| GET | `/api/places/{id}?user_id=` | Full AI-extracted state (−1) |
| POST | `/api/upload` | Form: `photo`, `user_id`, `lat`, `lng`, optional `place_id` |
| POST | `/api/ask` | JSON: `user_id`, `query`, optional `lat`, `lng` (−2) |
| GET | `/api/leaderboard` | Top scouts |
| GET | `/api/status` | Demo / mock flags and all rules |

## Project layout

```
backend/
  main.py       FastAPI app and endpoints
  privacy.py    face blur + metadata strip (runs before any AI call)
  vision.py     OpenRouter calls: photo analysis and Ask Scout
  credits.py    freshness, bounty pricing, GPS distance
  store.py      in-memory store (no photos, ever)
  places.json   seed places on CMU campus (coordinates approximate)
frontend/       single page, no build step (Tailwind + Leaflet via CDN)
shortcut/       how to build the one-tap iPhone Shortcut
```

## Tech

FastAPI · OpenCV · OpenRouter (Gemini 2.5 Flash by default, configurable) · Leaflet · Tailwind

## What is mocked / next steps

- Data lives in memory and resets on restart. Next: a database.
- Six seed places with approximate coordinates. Next: every building and room.
- Username + password accounts (salted PBKDF2 hashes). Next: CMU SSO so credits cannot be farmed with many accounts.
- Next: predictions ("Hunt is usually free after 8 pm") from the history of reports.

## Disclosure

All code was written during the hackathon. We used an AI coding assistant to help write it. The team's OpenRouter key powers only the app's own features.
