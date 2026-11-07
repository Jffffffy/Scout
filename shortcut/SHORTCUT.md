# iPhone Shortcut: one tap to snap and earn

The Shortcut takes a photo, adds your location, uploads it to Scout and shows how many credits you earned.

## 0. Make the server reachable from your phone

Your phone cannot reach `localhost` on a laptop. Expose the server with a tunnel:

```bash
# option A: cloudflared (no account needed)
cloudflared tunnel --url http://localhost:8000
# option B: ngrok
ngrok http 8000
```

Copy the `https://....` URL it prints. Below we call it `SCOUT_URL`.

## 1. Get your user ID

Open `SCOUT_URL` on the phone, tap the 🪙 balance in the top right, and copy your **user ID**.

## 2. Build the Shortcut (Shortcuts app ⟹ +)

| # | Action | Settings |
|---|---|---|
| 1 | **Take Photo** | Show Camera Preview: on |
| 2 | **Get Current Location** | |
| 3 | **Get Contents of URL** | URL: `SCOUT_URL/api/upload` · Method: **POST** · Request Body: **Form** |
| | Form field `photo` | type **File** ⟹ *Photo* (from step 1) |
| | Form field `user_id` | type **Text** ⟹ your user ID |
| | Form field `lat` | type **Text** ⟹ *Current Location* ⟹ **Latitude** |
| | Form field `lng` | type **Text** ⟹ *Current Location* ⟹ **Longitude** |
| 4 | **Get Dictionary Value** | Key: `message` from *Contents of URL* |
| 5 | **Show Notification** | *Dictionary Value* |

Name it **Scout Snap**.

## 3. Make it one tap

- Add it to the Home Screen (Share ⟹ Add to Home Screen), or
- Settings ⟹ Accessibility ⟹ Touch ⟹ **Back Tap** ⟹ Double Tap ⟹ *Scout Snap*, or
- On iPhone 15 Pro and later: assign it to the **Action Button**.

Android: use the **Snap & Earn** tab in the browser (it opens the camera directly) and add the page to the home screen.
