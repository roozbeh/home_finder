import Foundation
import Combine
import UIKit

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let sessionId = UUID().uuidString
    private let client: APIClient

    init(baseURL: String = AppConfig.baseURL) {
        self.client = APIClient(baseURL: baseURL)
        // Greeting is shown locally but never sent to the API (index 0 is always dropped)
        messages.append(.greeting)
    }

    // Full conversation history to send to the API.
    // Drops the first message (the local greeting) and excludes typing indicators.
    private var apiHistory: [APIMessage] {
        messages
            .dropFirst()
            .filter { $0.role == .user || $0.role == .assistant }
            .map { APIMessage(role: $0.role == .user ? "user" : "assistant", content: $0.text) }
    }

    func send(text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isLoading else { return }

        let userMessage = ChatMessage(role: .user, text: trimmed)
        messages.append(userMessage)
        errorMessage = nil
        isLoading = true

        let typingId = UUID()
        messages.append(ChatMessage(id: typingId, role: .typing, text: ""))

        // Capture history before entering async context
        let history = apiHistory

        Task {
            defer { isLoading = false }

            do {
                let response = try await client.sendMessage(history, sessionId: sessionId)
                removeMessage(id: typingId)

                if let apiError = response.error {
                    removeMessage(id: userMessage.id)
                    errorMessage = apiError
                    return
                }

                let rawText  = response.message ?? ""
                let listings = response.listings ?? []

                // Strip the OPEN_CALENDLY marker from the displayed text
                let displayText = rawText
                    .replacingOccurrences(of: "OPEN_CALENDLY", with: "")
                    .trimmingCharacters(in: .whitespacesAndNewlines)

                messages.append(ChatMessage(role: .assistant, text: displayText, listings: listings))

                if rawText.contains("OPEN_CALENDLY") {
                    await UIApplication.shared.open(AppConfig.calendlyURL)
                }

            } catch {
                removeMessage(id: typingId)
                removeMessage(id: userMessage.id)
                errorMessage = error.localizedDescription
            }
        }
    }

    func postFeedback(listingId: String, vote: FeedbackVote) {
        Task {
            await client.postFeedback(listingId: listingId, vote: vote, sessionId: sessionId)
        }
    }

    func dismissError() {
        errorMessage = nil
    }

    private func removeMessage(id: UUID) {
        messages.removeAll { $0.id == id }
    }
}
