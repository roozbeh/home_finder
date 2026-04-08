import SwiftUI
import GoogleSignIn

@main
struct HomeFinderApp: App {
    @StateObject private var vm = ChatViewModel()

    var body: some Scene {
        WindowGroup {
            ChatView()
                .environmentObject(vm)
                .onOpenURL { url in
                    GIDSignIn.sharedInstance.handle(url)
                }
        }
    }
}
