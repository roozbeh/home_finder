# HomeFinder iOS — Claude Context

## What This Is

A native SwiftUI iPhone app (**BayArea Home Finder** / **roozbeh.realtor**) built by Roozbeh Zabihollahi (DRE# 02225608). It is the iOS companion to the iPronto web chat UI. It talks to the same Flask backend (`mls_web` Docker container, port 8080 locally, port 80/443 on ipronto.net) and provides the exact same conversational experience — chat with Maya, get listing cards, thumbs up/down, book a showing.

No local logic — the app is a thin client over the backend API.

**iPhone only** — iPad is explicitly not supported. `Info.plist` should have `UIDeviceFamily = [1]` only.

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
    │   ├── Listing.swift             ← MLS listing fields + display helpers + custom Decodable
    │   └── FeedbackVote.swift        ← enum: .good | .bad
    ├── Networking/
    │   ├── APIClient.swift           ← URLSession: sendMessageStream(), login(), sessions, feedback
    │   ├── ChatRequest.swift         ← APIMessage + ChatRequest (Encodable)
    │   ├── ChatResponse.swift        ← ChatResponse + AuthResponse + ChatSession + ChatSessionDetail (Decodable)
    │   └── FeedbackRequest.swift     ← FeedbackRequest (Encodable)
    ├── ViewModels/
    │   └── ChatViewModel.swift       ← @MainActor ObservableObject — all state + logic + StoredUser
    └── Views/
        ├── ChatView.swift            ← root layout: header + ScrollView + InputBarView + SidebarView overlay
        ├── LoginView.swift           ← Sign in with Apple screen
        ├── MessageBubbleView.swift   ← single message row (user/assistant/typing)
        ├── ListingsScrollView.swift  ← horizontal ScrollView of ListingCardViews
        ├── ListingCardView.swift     ← property card with feedback buttons + photo
        ├── TypingIndicatorView.swift ← animated three-dot bounce
        ├── InputBarView.swift        ← TextEditor + send button (replaces old TextField)
        └── SidebarView.swift         ← left-drawer chat history with swipe-to-delete
```

## Backend API

The app talks to the Flask backend. The base URL is read from `Info.plist` key `API_BASE_URL`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/chat/stream` | POST | **Primary** — SSE streaming chat (used by iOS) |
| `/api/chat` | POST | Non-streaming fallback (not used by iOS currently) |
| `/api/feedback` | POST | Submit thumbs up/down on a listing |
| `/api/auth/login` | POST | Sign in / register — returns user_id, name, email |
| `/api/auth/me` | GET | Fetch user profile by user_id |
| `/api/sessions` | GET | List user's sessions (requires user_id param) |
| `/api/sessions/<id>` | GET | Fetch full session with messages |
| `/api/sessions/<id>` | DELETE | Delete a session |

### /api/chat/stream (SSE)

Request:
```json
{
  "messages": [{"role": "user", "content": "..."}, ...],
  "session_id": "uuid",
  "user_id": "uuid or empty string"
}
```

SSE events (one JSON object per `data:` line):
```json
{"type": "status",  "text": "Searching for properties..."}
{"type": "text",    "text": "word "}
{"type": "done",    "listings": [...], "full_text": "complete text"}
{"type": "error",   "text": "error message"}
```

### /api/auth/login

Request:
```json
{ "name": "...", "email": "...", "apple_user_id": "..." }
```
All fields optional but at least one identifier needed. Backend looks up by `apple_user_id` first, then `email`. Returns `{ "user_id": "...", "name": "...", "email": "..." }`.

**Important:** Apple only sends `email` on the very first sign-in. On subsequent sign-ins (or after app reinstall), `credential.email` is nil. Always send `credential.user` (Apple's stable ID) as `apple_user_id` so the backend can look up the existing account. This is why `apple_user_id` is the primary key.

## Key Architecture Decisions

### Single ViewModel (`ChatViewModel`)
`ChatViewModel` is `@MainActor ObservableObject`, instantiated once in `HomeFinderApp` as `@StateObject`, injected everywhere via `.environmentObject()`. All views declare `@EnvironmentObject var vm: ChatViewModel`. No per-view ViewModels.

### Streaming — Two-Bubble Pattern
The app uses SSE streaming via `client.sendMessageStream()` which returns an `AsyncThrowingStream<[String: Any], Error>`. Consumed with `for await event in ...` inside a `@MainActor Task` in `ChatViewModel.send()`.

Two bubbles are appended before the stream starts:
1. **textId bubble** — starts as `.typing` with empty text. Fills with words on `text` events. Becomes `.assistant` on first text chunk.
2. **dotsId bubble** — always `.typing` with empty text (shows animated dots). **Removed on `done`.**

This ensures the user sees dots below the streaming text until listings are fully loaded.

```swift
let textId = UUID(); let dotsId = UUID()
messages.append(ChatMessage(id: textId, role: .typing, text: ""))
messages.append(ChatMessage(id: dotsId, role: .typing, text: ""))

for try await event in client.sendMessageStream(history, sessionId: sid, userId: uid) {
    switch event["type"] as? String {
    case "status": updateMessage(id: textId, role: .typing, text: statusText, listings: [])
    case "text":   accumulatedText += chunk; updateMessage(id: textId, role: .assistant, ...)
                   scrollTrigger += 1
    case "done":   updateMessage(id: textId, role: .assistant, text: display, listings: finalListings)
                   removeMessage(id: dotsId); scrollTrigger += 1
    case "error":  removeMessage(id: textId); removeMessage(id: dotsId); errorMessage = ...
    }
}
```

### Actor Isolation — Critical
The whole module uses `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor`. The `for await` loop on `AsyncThrowingStream` inside a `Task {}` in a `@MainActor` method runs on the main actor — this is why UI updates work correctly. **Do not use callbacks from background threads** to update `@Published` vars — they will be silently ignored or crash. Use `AsyncThrowingStream` + `for await` instead.

### Auto-scroll
`scrollTrigger: Int` is incremented on `text` events (every word) and on `done`. `ChatView` observes it and scrolls to `"bottom"` anchor. Also scrolls on `messages.count` change and on `UIResponder.keyboardWillShowNotification`.

### Keyboard Handling
- `ChatView` does **not** use `.ignoresSafeArea(.keyboard, edges: .bottom)` — removing this modifier is what makes the input bar push up when the keyboard appears.
- `.scrollDismissesKeyboard(.interactively)` on the ScrollView lets users swipe down to dismiss keyboard.
- `InputBarView` sets `focused = false` on send to dismiss keyboard immediately.

### Input Bar — TextEditor not TextField
`InputBarView` uses `TextEditor` (not `TextField(axis: .vertical)`). `TextField(axis: .vertical)` triggers a per-keystroke system log "The variant selector cell index number could not be found" on physical devices — this is an iOS bug that cannot be fixed from app code. `TextEditor` avoids that code path. Since `TextEditor` has no built-in placeholder, a `ZStack` overlay shows `Color(.placeholderText)` text when empty.

### Dark Mode — Adaptive Colors
`MessageBubbleView` uses `Color(.secondarySystemBackground)` for bot bubble background and `Color(.label)` for bot text. These are iOS semantic colors that automatically flip for dark mode. **Do not use hardcoded RGB values** for message bubbles — they will be invisible in dark mode (the original bug was `Color(red: 0.94, green: 0.957, blue: 1.0)` background with `Color(red: 0.1, green: 0.1, blue: 0.17)` text both becoming near-white in dark mode).

### The Greeting Message
Maya's opening message is added locally in `ChatViewModel.init()` as the first entry in `messages[]`. It is **never sent to the API**. The `apiHistory` computed property always calls `.dropFirst()` to exclude it. This mirrors the web app's fix — the Anthropic API rejects message arrays that start with role `assistant`.

### Typing Indicator
The typing indicator is a first-class `ChatMessage` with `role == .typing`. When `text.isEmpty`, `MessageBubbleView` shows `TypingIndicatorView()` (animated dots). When `text` is non-empty, it shows the text in italic (used for "Searching for properties..." status messages).

### OPEN_CALENDLY
If the API response text contains the string `OPEN_CALENDLY`, the app opens `AppConfig.calendlyURL` in Safari and strips the marker from the displayed text. This matches the web app behavior.

### Feedback (thumbs up/down)
`ListingCardView` keeps `@State private var vote: FeedbackVote?` for immediate visual feedback. On tap it updates the local state instantly and fires `vm.postFeedback()` in a background Task — no waiting for the network response.

### Session History
`SidebarView` is a left-drawer overlay (slides in from leading edge). Uses `List` with `.onDelete` for swipe-to-delete. Sessions are loaded from `/api/sessions?user_id=...` when the sidebar opens. Individual sessions can be restored (full message history replayed into `messages[]`) or deleted.

### Session ID
`UUID().uuidString` generated once in `ChatViewModel.init()`. Not persisted — a new session starts each app launch. Sessions are stored server-side and accessible via the sidebar when signed in.

### Auth — Sign in with Apple
`LoginView` shows a `SignInWithAppleButton`. On success, calls `vm.signInWithApple(credential:)`.

`signInWithApple` always:
1. Reads `credential.user` (always present — Apple's stable user identifier)
2. Reads `credential.email` (nil on all but first sign-in) and `credential.fullName` (nil after first)
3. Falls back to `StoredUser.load()` for email/name if credential doesn't include them
4. Calls `client.login(name:email:appleUserId:)` — passes `credential.user` as `appleUserId`
5. Saves result as `StoredUser` to UserDefaults

`StoredUser` (in `ChatViewModel.swift`) stores `userId`, `name`, `email`, `appleUserId` in UserDefaults under key `"iprontoUser"`.

Debug logging is in place (`[SignIn]` prefix) — visible in Xcode console when testing on device.

### CITY formatting
MongoDB stores cities in ALL CAPS with no spaces (e.g. `FOSTERCITY`, `HALFMO BAY`, `EASTPAALTO`). `Listing.formattedCity` applies `.capitalized` which gives "Fostercity" — not perfect for run-together names but consistent with how the web app handles it.

### Listing Decoding — MongoDB Float/Int Issue
MongoDB stores integer fields (BEDROOMS_TOTAL, BATHROOMS_FULL) as floats (e.g. `2.0`). Swift's `JSONDecoder` fails when asked to decode `2.0` as `Int`. `Listing.swift` has a custom `init(from:)` that tries `Int` first, then `Double` fallback:
```swift
if let i = try? c.decodeIfPresent(Int.self, forKey: .bedroomsTotal) {
    bedroomsTotal = i
} else if let d = try? c.decodeIfPresent(Double.self, forKey: .bedroomsTotal) {
    bedroomsTotal = Int(d)
} else { bedroomsTotal = nil }
```

## Brand Colors

| Name | Hex | SwiftUI |
|------|-----|---------|
| Brand blue (primary) | `#092a56` | `Color(red: 0.035, green: 0.165, blue: 0.337)` |
| Brand light blue | `#1a4a8a` | `Color(red: 0.1, green: 0.29, blue: 0.54)` |
| Bot bubble bg (dark-mode adaptive) | system | `Color(.secondarySystemBackground)` |
| Bot bubble text (dark-mode adaptive) | system | `Color(.label)` |
| User bubble bg | `#092a56` | `Color(red: 0.035, green: 0.165, blue: 0.337)` |
| User bubble text | white | `.white` |

## Xcode / Build Setup

### Info.plist keys required
```xml
<!-- API base URL — change per target (simulator vs device vs production) -->
<key>API_BASE_URL</key>
<string>http://localhost:8080</string>

<!-- Allow HTTP (Flask runs plain HTTP) -->
<key>NSAppTransportSecurity</key>
<dict>
  <key>NSAllowsArbitraryLoads</key>
  <true/>
</dict>

<!-- iPhone only — suppress iPad screenshot requirement in App Store -->
<key>UIDeviceFamily</key>
<array>
  <integer>1</integer>
</array>

<!-- Suppress App Store export compliance warning -->
<key>ITSAppUsesNonExemptEncryption</key>
<false/>
```

### Physical Device vs Simulator
- Simulator: `API_BASE_URL = http://localhost:8080`
- Physical device (local): `API_BASE_URL = http://<mac-lan-ip>:8080` (find with `ipconfig getifaddr en0`)
- Production: `API_BASE_URL = https://ai.roozbeh.realtor` (or `http://ipronto.net:8080`)

### Build & Version Numbers
- **Version** (`CFBundleShortVersionString`) — user-facing, e.g. `1.0`
- **Build** (`CFBundleVersion`) — must be unique per App Store upload, increment each time (1, 2, 3...)
- Both found in Xcode → target → General → Identity

## Build & Xcode Gotchas Discovered

### Swift compiler issues (iOS 26 / Xcode 26)
This project targets iOS 26 with `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor` and `MemberImportVisibility` enabled in build settings. These cause non-obvious errors:

1. **`import Combine` is required** in `ChatViewModel.swift` — `@Published` and `ObservableObject` are no longer implicitly available with `MemberImportVisibility` on.
2. **`nonisolated` on `AppConfig.baseURL`** — the whole module is `@MainActor` by default, so `static var baseURL` becomes `@MainActor` and can't be used as a default parameter. Fixed with `nonisolated static var`.
3. **`UIApplication.shared.open()` is now `async`** in iOS 26 — requires `await`.
4. **`Color.tertiaryLabel` doesn't exist** — use `Color(UIColor.tertiaryLabel)` instead.
5. **`import UIKit` required** in files using `UIResponder`, `UIScreen`, `Color(.systemGray6)`, `Color(.label)`, `Color(.secondarySystemBackground)` etc. — SourceKit will flag these as errors if UIKit is not explicitly imported.

### SourceKit false positives
SourceKit (the IDE error checker) cannot resolve cross-file types like `ChatMessage`, `ChatViewModel`, `ListingsScrollView`, etc. in individual files. These show as red errors in the editor but **compile and run fine**. Do not be alarmed by SourceKit errors like "Cannot find type 'ChatMessage' in scope" — they are not real build errors.

### Duplicate file in Xcode project
When dragging files into Xcode, `HomeFinderApp.swift` ended up registered twice (once at root from Xcode's generated file, once in the Views group). This caused "Multiple commands produce HomeFinderApp.stringsdata". Fixed by removing the duplicate entries from `project.pbxproj`. If this happens again: in Xcode project navigator, look for duplicate file entries and delete one.

### Header branding
The header text is in `ChatView.swift` → `headerView`. Currently shows `roozbeh.realtor` and `Bay Area Home Finder · DRE# 02225608`.

## Known Issues / Harmless Logs

- **"The variant selector cell index number could not be found"** — appears once per keystroke in Xcode console on physical devices. iOS system log from the keyboard/emoji subsystem. Cannot be suppressed from app code. Has no effect on functionality. Ignore it.
- **SourceKit false positives** — see above. Compile and run to verify real errors vs IDE noise.

## App Store Status

- **Category:** Real Estate (primary), Lifestyle (secondary)
- **Subtitle:** `Chat to Find Your Home` (22 chars)
- **Privacy Policy:** `https://ai.roozbeh.realtor/privacy_policy`
- **Marketing URL:** `https://ai.roozbeh.realtor/about`
- **Support URL:** `https://ai.roozbeh.realtor/support`
- TestFlight set up. External testers can be invited via App Store Connect → TestFlight → External Testing → add testers by email.
- Screenshots required: iPhone only (6.7-inch). iPad not required because `UIDeviceFamily = [1]`.

## What Does Not Exist Yet

- Push notifications for new matching listings
- Offline / cached listings
- Deep link from listing card into a native detail view (currently links to the web `/listing/<id>`)
- Unit or UI tests for the iOS app
- Full-size listing photo gallery in-app (AsyncImage loads thumbnails; detail view links to web)
