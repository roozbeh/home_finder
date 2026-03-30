import Foundation
import Combine
import UIKit
import AuthenticationServices
import StoreKit

@MainActor
final class ChatViewModel: ObservableObject {
    // ── Chat state ────────────────────────────────────────────────────────────
    @Published var messages: [ChatMessage] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var scrollTrigger = 0

    // ── Auth state ────────────────────────────────────────────────────────────
    @Published var currentUser: StoredUser?
    @Published var showingLogin = false

    // ── Sidebar / session history ──────────────────────────────────────────────
    @Published var sessions: [ChatSession] = []
    @Published var sidebarOpen = false
    @Published var isLoadingSessions = false

    private(set) var sessionId = UUID().uuidString
    let client: APIClient

    init(baseURL: String = AppConfig.baseURL) {
        self.client = APIClient(baseURL: baseURL)
        self.currentUser = StoredUser.load()
        messages.append(.greeting)
    }

    // ── API history (drops greeting + typing indicators) ──────────────────────
    private var apiHistory: [APIMessage] {
        messages
            .dropFirst()
            .filter { $0.role == .user || $0.role == .assistant }
            .map { APIMessage(role: $0.role == .user ? "user" : "assistant", content: $0.text) }
    }

    // MARK: - Send

    func send(text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isLoading else { return }

        let userMessage = ChatMessage(role: .user, text: trimmed)
        messages.append(userMessage)
        errorMessage = nil
        isLoading = true

        // Two bubbles: text bubble (fills as chunks arrive) + persistent dots below it
        let textId  = UUID()
        let dotsId  = UUID()
        messages.append(ChatMessage(id: textId, role: .typing, text: ""))
        messages.append(ChatMessage(id: dotsId, role: .typing, text: ""))

        let history = apiHistory
        let sid     = sessionId
        let uid     = currentUser?.userId ?? ""

        Task {
            defer { isLoading = false }
            do {
                var accumulatedText = ""
                var finalListings: [Listing] = []

                for try await event in client.sendMessageStream(history, sessionId: sid, userId: uid) {
                    let type = event["type"] as? String ?? ""

                    switch type {
                    case "status":
                        let statusText = event["text"] as? String ?? ""
                        updateMessage(id: textId, role: .typing, text: statusText, listings: [])

                    case "text":
                        let chunk = event["text"] as? String ?? ""
                        accumulatedText += chunk
                        let display = accumulatedText
                            .replacingOccurrences(of: #"OPEN_CALENDLY\S*"#, with: "",
                                                  options: .regularExpression)
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                        updateMessage(id: textId, role: .assistant, text: display, listings: [])
                        scrollTrigger += 1

                    case "done":
                        let rawText = event["full_text"] as? String ?? accumulatedText
                        if let listingsRaw = event["listings"] as? [[String: Any]],
                           let listingsData = try? JSONSerialization.data(withJSONObject: listingsRaw),
                           let decoded = try? JSONDecoder().decode([Listing].self, from: listingsData) {
                            finalListings = decoded
                        }
                        let display = rawText
                            .replacingOccurrences(of: #"OPEN_CALENDLY\S*"#, with: "",
                                                  options: .regularExpression)
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                        updateMessage(id: textId, role: .assistant, text: display, listings: finalListings)
                        removeMessage(id: dotsId)
                        scrollTrigger += 1

                        if rawText.contains("OPEN_CALENDLY") {
                            Task { await UIApplication.shared.open(AppConfig.calendlyURL) }
                        }

                        // Track engagement for review prompt
                        let sent = UserDefaults.standard.integer(forKey: "totalMessagesSent") + 1
                        UserDefaults.standard.set(sent, forKey: "totalMessagesSent")
                        if sent >= 3 { requestReviewIfEligible() }

                    case "error":
                        let errText = event["text"] as? String ?? "Unknown error"
                        removeMessage(id: textId)
                        removeMessage(id: dotsId)
                        removeMessage(id: userMessage.id)
                        errorMessage = errText

                    default:
                        break
                    }
                }
            } catch {
                removeMessage(id: textId)
                removeMessage(id: dotsId)
                removeMessage(id: userMessage.id)
                errorMessage = error.localizedDescription
            }
        }
    }

    // MARK: - Feedback

    func postFeedback(listingId: String, vote: FeedbackVote) {
        let uid = currentUser?.userId ?? ""
        Task {
            await client.postFeedback(listingId: listingId, vote: vote,
                                      sessionId: sessionId, userId: uid)
        }
        // A thumbs-up is a strong positive signal — good time to ask for a review
        if vote == .good { requestReviewIfEligible() }
    }

    // MARK: - Review prompt

    private func requestReviewIfEligible() {
        let lastKey = "lastReviewRequestDate"
        let now     = Date()
        // Respect Apple's guideline: don't ask more than once per 90 days
        if let last = UserDefaults.standard.object(forKey: lastKey) as? Date,
           now.timeIntervalSince(last) < 90 * 24 * 3600 { return }
        guard let windowScene = UIApplication.shared.connectedScenes
            .first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene
        else { return }
        SKStoreReviewController.requestReview(in: windowScene)
        UserDefaults.standard.set(now, forKey: lastKey)
    }

    // MARK: - New chat

    func startNewChat() {
        sessionId = UUID().uuidString
        messages = [.greeting]
        sidebarOpen = false
    }

    // MARK: - Session history

    func loadSessions() {
        guard let uid = currentUser?.userId else { return }
        isLoadingSessions = true
        Task {
            defer { isLoadingSessions = false }
            do {
                sessions = try await client.fetchSessions(userId: uid)
            } catch {
                // silently fail — sidebar just shows empty
            }
        }
    }

    func restoreSession(_ session: ChatSession) {
        Task {
            do {
                let detail = try await client.fetchSession(sessionId: session.sessionId)
                sessionId = session.sessionId
                var restored: [ChatMessage] = [.greeting]
                for m in detail.messages {
                    let role: MessageRole = m.role == "user" ? .user : .assistant
                    restored.append(ChatMessage(role: role, text: m.content,
                                                listings: m.listings ?? []))
                }
                messages = restored
                sidebarOpen = false
            } catch {
                errorMessage = "Couldn't load that conversation."
            }
        }
    }

    func deleteSession(_ session: ChatSession) {
        Task {
            await client.deleteSession(sessionId: session.sessionId)
            sessions.removeAll { $0.sessionId == session.sessionId }
        }
    }

    // MARK: - Auth

    func signInWithApple(credential: ASAuthorizationAppleIDCredential) {
        let appleUserId = credential.user  // stable, always present
        let name  = [credential.fullName?.givenName, credential.fullName?.familyName]
            .compactMap { $0 }.joined(separator: " ")
        let email = credential.email ?? ""

        print("[SignIn] credential.user      = \(appleUserId)")
        print("[SignIn] credential.email     = \(email.isEmpty ? "<nil>" : email)")
        print("[SignIn] credential.fullName  = \(name.isEmpty ? "<nil>" : name)")

        // Apple only sends email/name on the very first sign-in.
        // Fall back to stored values for subsequent logins or after reinstall.
        let stored = StoredUser.load()
        print("[SignIn] StoredUser.load()    = \(stored == nil ? "nil" : "userId=\(stored!.userId) email=\(stored!.email) appleUserId=\(stored!.appleUserId)")")

        let resolvedEmail = !email.isEmpty ? email : (stored?.email ?? "")
        let resolvedName  = !name.isEmpty  ? name  : (stored?.name  ?? "Apple User")

        print("[SignIn] resolvedEmail        = \(resolvedEmail.isEmpty ? "<empty>" : resolvedEmail)")
        print("[SignIn] resolvedName         = \(resolvedName)")

        Task {
            do {
                print("[SignIn] calling /api/auth/login ...")
                let resp = try await client.login(
                    name: resolvedName,
                    email: resolvedEmail,
                    appleUserId: appleUserId
                )
                print("[SignIn] login success: user_id=\(resp.user_id) name=\(resp.name) email=\(resp.email)")
                let user = StoredUser(userId: resp.user_id, name: resp.name,
                                      email: resp.email, appleUserId: appleUserId)
                user.save()
                currentUser = user
                showingLogin = false
                loadSessions()
            } catch {
                print("[SignIn] login error: \(error)")
                errorMessage = "Sign-in failed. Please try again."
            }
        }
    }

    func signOut() {
        StoredUser.clear()
        currentUser = nil
        sessions    = []
        startNewChat()
    }

    // MARK: - Helpers

    func dismissError() { errorMessage = nil }

    private func removeMessage(id: UUID) {
        messages.removeAll { $0.id == id }
    }

    private func updateMessage(id: UUID, role: MessageRole, text: String, listings: [Listing]) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[idx] = ChatMessage(id: id, role: role, text: text, listings: listings)
    }
}

// MARK: - StoredUser (Keychain-backed simple user)

struct StoredUser {
    let userId: String
    let name: String
    let email: String
    let appleUserId: String

    private static let key = "iprontoUser"

    func save() {
        let dict: [String: String] = [
            "userId": userId, "name": name, "email": email, "appleUserId": appleUserId
        ]
        if let data = try? JSONEncoder().encode(dict) {
            UserDefaults.standard.set(data, forKey: StoredUser.key)
        }
    }

    static func load() -> StoredUser? {
        guard let data = UserDefaults.standard.data(forKey: key),
              let dict = try? JSONDecoder().decode([String: String].self, from: data),
              let userId = dict["userId"], let name = dict["name"]
        else { return nil }
        return StoredUser(userId: userId, name: name,
                          email: dict["email"] ?? "",
                          appleUserId: dict["appleUserId"] ?? "")
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: key)
    }
}
