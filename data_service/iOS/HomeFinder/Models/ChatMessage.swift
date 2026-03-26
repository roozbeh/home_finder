import Foundation

enum MessageRole {
    case user
    case assistant
    case typing  // placeholder shown while waiting for the API response
}

struct ChatMessage: Identifiable {
    let id: UUID
    let role: MessageRole
    let text: String
    let listings: [Listing]

    init(id: UUID = UUID(), role: MessageRole, text: String, listings: [Listing] = []) {
        self.id = id
        self.role = role
        self.text = text
        self.listings = listings
    }

    /// The initial greeting shown locally — never sent to the API.
    static let greeting = ChatMessage(
        role: .assistant,
        text: "Hi there! I'm Maya, your Bay Area home finding assistant. 👋\n\nI'm here to help you find the perfect home. Tell me — what are you looking for? For example, are you searching for a family home, a starter home, or something specific in mind?\n\nTip: Sign in to save your conversations and pick up right where you left off — across all your devices."
    )
}
