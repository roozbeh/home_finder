import Foundation

enum AppConfig {
    /// Set API_BASE_URL in Info.plist.
    /// Simulator: http://localhost:8080
    /// Physical device: http://192.168.x.x:8080  (your Mac's LAN IP)
    nonisolated static var baseURL: String {
        Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String
            ?? "http://localhost:8080"
    }

    static let calendlyURL = URL(string: "https://calendly.com/ruzbeh-o0w7/new-meeting")!
}
