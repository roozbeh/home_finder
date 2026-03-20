# iPronto iOS — Home Finder Chat App

Conversational home-finder powered by the Maya agent backend.

## Setup in Xcode

1. Open Xcode → **File → New → Project** → iOS App
   - Interface: SwiftUI
   - Language: Swift
   - Product Name: `iPronto`

2. Delete the generated `ContentView.swift`

3. Drag **all files** from `iOS/HomeFinder/` into the Xcode project navigator
   (check "Copy items if needed" → Finish)

4. In `Info.plist`, add two entries:

   | Key | Value |
   |-----|-------|
   | `API_BASE_URL` | `http://localhost:8080` (simulator) or `http://192.168.x.x:8080` (device) |
   | `NSAppTransportSecurity` → `NSAllowsArbitraryLoads` | `YES` |

   The ATS entry is required because the Flask server runs plain HTTP, not HTTPS.

5. Build & Run on the Simulator (⌘R)

## Physical Device

The app must reach the Docker host over the network. Find your Mac's LAN IP:

```bash
ipconfig getifaddr en0
```

Set `API_BASE_URL` in Info.plist to `http://<that-ip>:8080`.
Make sure the device and Mac are on the same Wi-Fi network.

## Talking to the Backend

The app calls two endpoints:

| Endpoint | Used for |
|----------|----------|
| `POST /api/chat` | Send messages, get Maya's reply + listings |
| `POST /api/feedback` | Thumbs up / down on a listing |

The backend must be running (`docker compose up`) before launching the app.
