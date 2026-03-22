import Foundation

struct ChatSession: Codable, Identifiable {
    let sessionId: String
    let userId: String
    let title: String
    let updatedAt: String?
    let createdAt: String?

    var id: String { sessionId }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case userId    = "user_id"
        case title
        case updatedAt = "updated_at"
        case createdAt = "created_at"
    }
}

struct StoredMessage: Codable {
    let role: String
    let content: String
    let listings: [Listing]?
}

struct ChatSessionDetail: Codable {
    let sessionId: String
    let messages: [StoredMessage]

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case messages
    }
}
