# 🧭 Scout: the Waze for campus spaces

 ## Team
   - Jessica Fang (@Jffffffy)
   - Helen Zhou (@helennnzmay)
   - Zoe Yu (@yuzoe29)
   - Kelly Zhang (@zhangkelly1206)

## 🔗 Try it: https://scout-17ly.onrender.com

Open it on your phone, sign up, and snap a place. (Free server: if nobody used it for 15 minutes, the first visit takes about 50 seconds to wake up.)

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

### Busy levels (not busy / moderate / busy)

The AI counts seats in each photo: `total_seats` and `occupied_seats` (a person or belongings on it).
All fresh photos of one area are **added before dividing**:

```
photo 1: 8/10 taken, photo 2: 2/10 taken  ->  (8+2)/(10+10) = 50%  ->  moderate
```

| Occupancy | Level |
|---|---|
| < 40% | not busy |
| 40–70% | moderate |
| > 70% | busy |

Dining also uses the line: ≤ 3 people not busy, ≤ 10 moderate, more is busy.

### Photos other students can see

Original photo ⟹ faces blurred ⟹ AI finds every person and every empty seat ⟹ each person is covered by a
gray block (whole body) and each free seat is outlined in green. Only this covered copy is kept, and only until
the place goes stale (max 30 min). Viewing an area's summary and photos costs 1 credit (free again for 30 min).

### Students grow the map

- **Before upload** the app guesses the place from GPS and shows it, so the user can change it before the AI runs.
- **After upload**, "Wrong place?" moves the photo to the right place within 10 minutes (the AI re-checks it).
- **Not on the map?** Add it: name, category, location (GPS / building search / tap the map), optional room.
  New places show 🆕 until someone else's photo there passes the AI check, then the creator earns +2.
  A similar name of the same type within 30 m triggers "Is it X?" to avoid duplicates.

### Fix a wrong pin (crowd-sourced map)

Pins start at approximate coordinates. Anyone can tap **📍 Wrong location?** and suggest the right spot by
searching a building (OpenStreetMap, limited to campus), tapping the map, or using their GPS, plus an optional
floor / room note. When **2 different people** suggest spots within **40 m**, the pin moves to their midpoint and
both earn **+2**. One suggestion per person per place. `GET /api/places-export` returns the corrected list in
`places.json` format so fixes can be saved permanently.

### Freshness

| Place type | Goes stale after |
|---|---|
| Dining | 10 min |
| Gym, office hours | 15 min |
| Study spaces, dorm lounges | 30 min |
| Laundry | when the next running machine should finish (max 30 min) |

### Anti-abuse

- GPS must be within **80 m** of the place (turned off when `DEMO_MODE=true`)
- One paid upload per person per place every **10 min**
- The vision model rejects screenshots, photos of screens, stock images, selfies and wrong places

### Privacy by design

1. Faces are blurred **before** the photo is sent to any AI model. If the face detector is unavailable, the upload is refused.
2. Re-encoding removes EXIF metadata (GPS, device info).
3. The original photo only lives in memory during the request and is never written to disk. A face-blurred copy
   (no metadata) is kept in memory for 10 minutes only, so the uploader can fix a wrong place and the AI can re-check it. Other students only ever see a copy with faces blurred and people covered, and it is deleted when the place goes stale.
4. The model is told never to describe people.

---

## Hosting

Live at **https://scout-17ly.onrender.com**, deployed on Render from this repo (config in `render.yaml`).
Every push to `main` redeploys automatically. The OpenRouter key lives only in Render's environment settings,
never in the code. Data is kept in memory, so it resets on each redeploy.

Phone one-tap uploads: see [`shortcut/SHORTCUT.md`](shortcut/SHORTCUT.md).

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/api/signup` | JSON `username`, `password` ⟹ account (+5 credits) |
| POST | `/api/login` | JSON `username`, `password` ⟹ account |
| GET | `/api/places` | Map data: color, freshness, bounty (free) |
| GET | `/api/places/{id}?user_id=` | Area summary + list of fresh photos (−1, free again for 30 min) |
| GET | `/api/reports/{id}/photo?user_id=` | Blurred, covered photo (uploader, or after unlocking the place) |
| POST | `/api/upload` | Form: `photo`, `user_id`, `lat`, `lng`, optional `place_id` |
| POST | `/api/ask` | JSON: `user_id`, `query`, optional `lat`, `lng` (−2) |
| POST | `/api/places` | Add a new place (JSON `user_id`, `name`, `category`, `lat`, `lng`, optional `note`, `force`) |
| POST | `/api/reports/{id}/move` | Move your photo to the right place within 10 min (JSON `user_id`, `place_id`) |
| POST | `/api/places/{id}/fix` | JSON `user_id`, `lat`, `lng`, optional `note`: suggest the right spot |
| GET | `/api/places-export` | Places with crowd-corrected coordinates |
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
  places.json   39 CMU places: all dining areas (official list), study areas, dorm lounges (coordinates approximate)
frontend/       single page, no build step (Tailwind + Leaflet via CDN)
shortcut/       how to build the one-tap iPhone Shortcut
```

## Tech

FastAPI · OpenCV · OpenRouter (Gemini Omni 1.2 Flash by default with automatic fallback to Gemini 2.5 Flash, configurable) · Leaflet · Tailwind

## What is mocked / next steps

- Data lives in memory and resets on restart. Next: a database.
- 39 places with approximate coordinates. A few photos stand in for a whole area. Next: more photos per area and floor plans.
- Username + password accounts (salted PBKDF2 hashes). Next: CMU SSO so credits cannot be farmed with many accounts.
- Next: predictions ("Hunt is usually free after 8 pm") from the history of reports.

## Disclosure

All code was written during the hackathon. We used an AI coding assistant to help write it. The team's OpenRouter key powers only the app's own features.
