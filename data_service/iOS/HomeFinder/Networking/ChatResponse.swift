struct ChatResponse: Decodable {
    let message: String?
    let listings: [Listing]?
    let session_id: String?
    let error: String?
}
