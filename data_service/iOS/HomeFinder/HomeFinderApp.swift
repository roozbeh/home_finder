import SwiftUI

@main
struct HomeFinderApp: App {
    @StateObject private var vm = ChatViewModel()

    var body: some Scene {
        WindowGroup {
            if vm.currentUser != nil {
                ChatView()
                    .environmentObject(vm)
            } else {
                LoginView()
                    .environmentObject(vm)
            }
        }
    }
}
