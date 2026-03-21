import Foundation

struct APIClient {
    let baseURL: String

    func sendMessage(_ messages: [APIMessage], sessionId: String, userId: String = "") async throws -> ChatResponse {
        guard let url = URL(string: "\(baseURL)/api/chat") else {
            throw URLError(.badURL)
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 60

        request.httpBody = try JSONEncoder().encode(
            ChatRequest(messages: messages, session_id: sessionId, user_id: userId)
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

    func postFeedback(listingId: String, vote: FeedbackVote, sessionId: String, userId: String = "") async {
        guard let url = URL(string: "\(baseURL)/api/feedback") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = FeedbackRequest(listing_id: listingId, feedback: vote.rawValue,
                                   session_id: sessionId, user_id: userId)
        guard let data = try? JSONEncoder().encode(body) else { return }
        request.httpBody = data

        _ = try? await URLSession.shared.data(for: request)
    }

    // MARK: - Auth

    func login(name: String, email: String) async throws -> AuthResponse {
        guard let url = URL(string: "\(baseURL)/api/auth/login") else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["name": name, "email": email])
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(AuthResponse.self, from: data)
    }

    // MARK: - Sessions

    func fetchSessions(userId: String) async throws -> [ChatSession] {
        guard let url = URL(string: "\(baseURL)/api/sessions?user_id=\(userId)") else { throw URLError(.badURL) }
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode([ChatSession].self, from: data)
    }

    func fetchSession(sessionId: String) async throws -> ChatSessionDetail {
        guard let url = URL(string: "\(baseURL)/api/sessions/\(sessionId)") else { throw URLError(.badURL) }
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(ChatSessionDetail.self, from: data)
    }

    func deleteSession(sessionId: String) async {
        guard let url = URL(string: "\(baseURL)/api/sessions/\(sessionId)") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: req)
    }
}
