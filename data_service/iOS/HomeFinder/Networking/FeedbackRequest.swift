struct FeedbackRequest: Encodable {
    let listing_id: String
    let feedback: String   // "good" or "bad"
    let session_id: String
}
