import Foundation
import Combine
import UIKit
import AuthenticationServices

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
        let name  = [credential.fullName?.givenName, credential.fullName?.familyName]
            .compactMap { $0 }.joined(separator: " ")
        let email = credential.email ?? ""

        // Apple only sends email on first sign-in; fall back to stored value
        let resolvedEmail = email.isEmpty ? (StoredUser.load()?.email ?? "") : email
        let resolvedName  = name.isEmpty  ? (StoredUser.load()?.name  ?? "User") : name

        guard !resolvedEmail.isEmpty else {
            errorMessage = "Could not retrieve email from Apple. Please try again."
            return
        }

        Task {
            do {
                let resp = try await client.login(name: resolvedName, email: resolvedEmail)
                let user = StoredUser(userId: resp.user_id, name: resp.name, email: resp.email)
                user.save()
                currentUser = user
                showingLogin = false
                loadSessions()
            } catch {
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

    private static let key = "iprontoUser"

    func save() {
        let dict: [String: String] = ["userId": userId, "name": name, "email": email]
        if let data = try? JSONEncoder().encode(dict) {
            UserDefaults.standard.set(data, forKey: StoredUser.key)
        }
    }

    static func load() -> StoredUser? {
        guard let data = UserDefaults.standard.data(forKey: key),
              let dict = try? JSONDecoder().decode([String: String].self, from: data),
              let userId = dict["userId"], let name = dict["name"], let email = dict["email"]
        else { return nil }
        return StoredUser(userId: userId, name: name, email: email)
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: key)
    }
}
