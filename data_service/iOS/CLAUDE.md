# HomeFinder iOS — Claude Context

## What This Is

A native SwiftUI iPhone app that is the iOS companion to the iPronto web chat UI. It talks to the same Flask backend (`mls_web` Docker container on port 8080) and provides the exact same conversational experience — chat with Maya, get listing cards, thumbs up/down, book a showing.

No local logic — the app is a thin client over the backend API.

## File Structure

```
iOS/
├── CLAUDE.md                        ← you are here
├── README.md                        ← Xcode setup instructions for humans
└── HomeFinder/
    ├── HomeFinderApp.swift           ← @main entry point, injects ChatViewModel
    ├── Config/
    │   └── AppConfig.swift           ← API_BASE_URL (from Info.plist) + Calendly URL
    ├── Models/
    │   ├── ChatMessage.swift         ← ChatMessage struct + MessageRole enum + .greeting constant
    │   ├── Listing.swift             ← MLS listing fields + display helpers
    │   └── FeedbackVote.swift        ← enum: .good | .bad
    ├── Networking/
    │   ├── APIClient.swift           ← URLSession: sendMessage(), postFeedback()
    │   ├── ChatRequest.swift         ← APIMessage + ChatRequest (Encodable)
    │   ├── ChatResponse.swift        ← ChatResponse (Decodable)
    │   └── FeedbackRequest.swift     ← FeedbackRequest (Encodable)
    ├── ViewModels/
    │   └── ChatViewModel.swift       ← @MainActor ObservableObject — all state + logic
    └── Views/
        ├── ChatView.swift            ← root layout: header + ScrollView + InputBarView
        ├── MessageBubbleView.swift   ← single message row (user/assistant/typing)
        ├── ListingsScrollView.swift  ← horizontal ScrollView of ListingCardViews
        ├── ListingCardView.swift     ← property card with feedback buttons
        ├── TypingIndicatorView.swift ← animated three-dot bounce
        └── InputBarView.swift        ← text field + send button
```

## Backend API

The app talks to the Flask backend. The base URL is read from `Info.plist` key `API_BASE_URL`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/chat` | POST | Send message history, get reply + listings |
| `/api/feedback` | POST | Submit thumbs up/down on a listing |

### /api/chat

Request:
```json
{
  "messages": [{"role": "user", "content": "..."}, ...],
  "session_id": "uuid"
}
```

Response:
```json
{
  "message": "Maya's reply text",
  "listings": [ ...listing objects... ],
  "session_id": "uuid"
}
```

The `listings` array may be empty. Each listing has MLS fields in ALL CAPS: `LISTING_ID`, `STREET_ADDRESS`, `CITY`, `LIST_PRICE`, `BEDROOMS_TOTAL`, `BATHROOMS_FULL`, `BATHS_DISPLAY`, `SQFT`, `MLS_STATUS`.

### /api/feedback

Request:
```json
{
  "listing_id": "...",
  "feedback": "good" | "bad",
  "session_id": "uuid"
}
```

## Key Architecture Decisions

### Single ViewModel (`ChatViewModel`)
`ChatViewModel` is `@MainActor ObservableObject`, instantiated once in `HomeFinderApp` as `@StateObject`, injected everywhere via `.environmentObject()`. All views declare `@EnvironmentObject var vm: ChatViewModel`. No per-view ViewModels.

### The Greeting Message
Maya's opening message is added locally in `ChatViewModel.init()` as the first entry in `messages[]`. It is **never sent to the API**. The `apiHistory` computed property always calls `.dropFirst()` to exclude it. This mirrors the web app's fix — the Anthropic API rejects message arrays that start with role `assistant`.

### Typing Indicator
The typing indicator is a first-class `ChatMessage` with `role == .typing`. It is inserted into `messages[]` before the network call and removed on response (or error). This keeps the `LazyVStack` data source as a single array with no extra state flags.

### OPEN_CALENDLY
If the API response text contains the string `OPEN_CALENDLY`, the app opens `AppConfig.calendlyURL` in Safari and strips the marker from the displayed text. This matches the web app behavior.

### Feedback (thumbs up/down)
`ListingCardView` keeps `@State private var vote: FeedbackVote?` for immediate visual feedback. On tap it updates the local state instantly and fires `vm.postFeedback()` in a background Task — no waiting for the network response.

### Session ID
`UUID().uuidString` generated once in `ChatViewModel.init()`. Not persisted — a new session starts each app launch, matching the web app.

### CITY formatting
MongoDB stores cities in ALL CAPS with no spaces (e.g. `FOSTERCITY`, `HALFMO BAY`, `EASTPAALTO`). `Listing.formattedCity` applies `.capitalized` which gives "Fostercity" — not perfect for run-together names but consistent with how the web app handles it.

## Xcode Setup Gotchas

### App Transport Security (ATS)
The Flask server runs plain HTTP. iOS blocks non-HTTPS by default. Must add to `Info.plist`:
```xml
<key>NSAppTransportSecurity</key>
<dict>
  <key>NSAllowsArbitraryLoads</key>
  <true/>
</dict>
```

### Physical Device vs Simulator
- Simulator: `API_BASE_URL = http://localhost:8080`
- Physical device: `API_BASE_URL = http://<mac-lan-ip>:8080` (find with `ipconfig getifaddr en0`)

### Creating the Xcode Project
1. File → New → Project → iOS App, SwiftUI, Swift, name it `HomeFinder`
2. Delete generated `ContentView.swift`
3. Drag all files from `iOS/HomeFinder/` into the project navigator (copy items checked)
4. Add `API_BASE_URL` and ATS exception to `Info.plist`
5. Build with ⌘R

## Brand Colors

| Name | Hex | SwiftUI |
|------|-----|---------|
| Brand blue (primary) | `#092a56` | `Color(red: 0.035, green: 0.165, blue: 0.337)` |
| Brand light blue | `#1a4a8a` | `Color(red: 0.1, green: 0.29, blue: 0.54)` |
| Bubble background | `#f0f4ff` | `Color(red: 0.94, green: 0.957, blue: 1.0)` |

## Build & Xcode Gotchas Discovered

### Swift compiler issues fixed (iOS 26 / Xcode 26)
This project targets iOS 26 with `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor` and `MemberImportVisibility` enabled in build settings. These cause non-obvious errors:

1. **`import Combine` is required** in `ChatViewModel.swift` — `@Published` and `ObservableObject` are no longer implicitly available with `MemberImportVisibility` on.
2. **`nonisolated` on `AppConfig.baseURL`** — the whole module is `@MainActor` by default, so `static var baseURL` becomes `@MainActor` and can't be used as a default parameter. Fixed with `nonisolated static var`.
3. **`UIApplication.shared.open()` is now `async`** in iOS 26 — requires `await`.
4. **`Color.tertiaryLabel` doesn't exist** — use `Color(UIColor.tertiaryLabel)` instead.

### Duplicate file in Xcode project
When dragging files into Xcode, `HomeFinderApp.swift` ended up registered twice (once at root from Xcode's generated file, once in the Views group). This caused "Multiple commands produce HomeFinderApp.stringsdata". Fixed by removing the duplicate entries from `project.pbxproj`. If this happens again: in Xcode project navigator, look for duplicate file entries and delete one.

### Header branding
The header text is in `ChatView.swift` → `headerView`. Currently shows `roozbeh.realtor` and `Bay Area Home Finder`.

## What Does Not Exist Yet

- App icon (`Assets.xcassets` exists but no icon image — see README for tooling suggestions)
- Push notifications for new matching listings
- Offline / cached listings
- Deep link from listing card into a native detail view (currently links to the web `/listing/<id>`)
- Unit or UI tests for the iOS app
- Listing photos (backend doesn't have them yet either)
