import Foundation

struct APIClient {
    let baseURL: String

    func sendMessage(_ messages: [APIMessage], sessionId: String) async throws -> ChatResponse {
        guard let url = URL(string: "\(baseURL)/api/chat") else {
            throw URLError(.badURL)
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 60

        request.httpBody = try JSONEncoder().encode(
            ChatRequest(messages: messages, session_id: sessionId)
        )

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }

        // On non-200, try to surface the server's error message
        if http.statusCode != 200 {
            if let errResp = try? JSONDecoder().decode(ChatResponse.self, from: data),
               let msg = errResp.error {
                throw NSError(domain: "API", code: http.statusCode,
                              userInfo: [NSLocalizedDescriptionKey: msg])
            }
            throw URLError(.badServerResponse)
        }

        return try JSONDecoder().decode(ChatResponse.self, from: data)
    }

    func postFeedback(listingId: String, vote: FeedbackVote, sessionId: String) async {
        guard let url = URL(string: "\(baseURL)/api/feedback") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = FeedbackRequest(listing_id: listingId, feedback: vote.rawValue, session_id: sessionId)
        guard let data = try? JSONEncoder().encode(body) else { return }
        request.httpBody = data

        _ = try? await URLSession.shared.data(for: request)
    }
}
