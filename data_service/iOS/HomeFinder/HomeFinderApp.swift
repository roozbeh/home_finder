import SwiftUI

@main
struct HomeFinderApp: App {
    @StateObject private var vm = ChatViewModel()

    var body: some Scene {
        WindowGroup {
            ChatView()
                .environmentObject(vm)
        }
    }
}
