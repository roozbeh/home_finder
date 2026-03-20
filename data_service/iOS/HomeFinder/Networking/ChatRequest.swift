struct APIMessage: Codable {
    let role: String
    let content: String
}

struct ChatRequest: Encodable {
    let messages: [APIMessage]
    let session_id: String
}
