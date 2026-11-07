# iPhone Shortcut: one tap to snap and earn

The Shortcut takes a photo, adds your location, uploads it to Scout and shows how many credits you earned.

## 0. The link

`SCOUT_URL` = **https://scout-17ly.onrender.com**

## 1. Get your user ID

Open `SCOUT_URL` on the phone, sign up or log in, tap the 🪙 balance in the top right, and copy your **private Shortcut key**. Use it as `user_id` below. Keep it secret: anyone with it can spend your credits.

## 2. Build the Shortcut (Shortcuts app ⟹ +)

| # | Action | Settings |
|---|---|---|
| 1 | **Take Photo** | Show Camera Preview: on |
| 2 | **Convert Image** | Convert *Photo* to **JPEG** · Preserve Metadata: **off** (iPhones shoot HEIC, which the server cannot read) |
| 3 | **Get Current Location** | |
| 4 | **Get Contents of URL** | URL: `SCOUT_URL/api/upload` · Method: **POST** · Request Body: **Form** |
| | Form field `photo` | type **File** ⟹ *Converted Image* (from step 2) |
| | Form field `user_id` | type **Text** ⟹ your private Shortcut key |
| | Form field `lat` | type **Text** ⟹ *Current Location* ⟹ **Latitude** |
| | Form field `lng` | type **Text** ⟹ *Current Location* ⟹ **Longitude** |
| 5 | **Get Dictionary Value** | Key: `message` from *Contents of URL* |
| 6 | **Show Notification** | *Dictionary Value* |

Name it **Scout Snap**.

## 3. Make it one tap

- Add it to the Home Screen (Share ⟹ Add to Home Screen), or
- Settings ⟹ Accessibility ⟹ Touch ⟹ **Back Tap** ⟹ Double Tap ⟹ *Scout Snap*, or
- On iPhone 15 Pro and later: assign it to the **Action Button**.

Android: use the **Snap & Earn** tab in the browser (it opens the camera directly) and add the page to the home screen.
