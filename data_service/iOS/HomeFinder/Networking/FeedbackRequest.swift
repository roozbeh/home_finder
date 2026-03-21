struct FeedbackRequest: Encodable {
    let listing_id: String
    let feedback: String   // "good" or "bad"
    let session_id: String
    let user_id: String
}

struct AuthResponse: Decodable {
    let user_id: String
    let name: String
    let email: String
}
