import SwiftUI

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct ChatView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ZStack(alignment: .leading) {
            // Main chat content
            VStack(spacing: 0) {
                headerView

                if let error = vm.errorMessage {
                    errorBanner(error)
                }

                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(vm.messages) { message in
                                MessageBubbleView(message: message)
                            }
                            Color.clear.frame(height: 1).id("bottom")
                        }
                        .padding(.vertical, 16)
                    }
                    .onChange(of: vm.messages.count) { _ in
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                            withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                        }
                    }
                }

                InputBarView()
            }
            .ignoresSafeArea(.keyboard, edges: .bottom)
            .background(Color(.systemGroupedBackground))

            // Sidebar overlay (ChatGPT-style left drawer)
            SidebarView()
        }
        .onAppear {
            if vm.currentUser != nil { vm.loadSessions() }
        }
    }

    // MARK: - Header

    private var headerView: some View {
        HStack(spacing: 10) {
            // Hamburger menu button
            Button(action: {
                withAnimation(.easeInOut(duration: 0.25)) {
                    vm.sidebarOpen.toggle()
                    if vm.sidebarOpen { vm.loadSessions() }
                }
            }) {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 20))
                    .foregroundColor(.white)
            }

            VStack(alignment: .leading, spacing: 1) {
                Text("roozbeh.realtor")
                    .font(.headline).fontWeight(.bold)
                    .foregroundColor(.white)
                Text("Bay Area Home Finder · DRE# 02225608")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.7))
            }

            Spacer()

            // User avatar / sign-in
            Button(action: {
                if vm.currentUser != nil {
                    withAnimation { vm.sidebarOpen = true; vm.loadSessions() }
                } else {
                    vm.showingLogin = true
                }
            }) {
                if let user = vm.currentUser {
                    Circle()
                        .fill(Color.white.opacity(0.25))
                        .frame(width: 32, height: 32)
                        .overlay(
                            Text(String(user.name.prefix(1)).uppercased())
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundColor(.white)
                        )
                } else {
                    Image(systemName: "person.circle")
                        .font(.system(size: 22))
                        .foregroundColor(.white.opacity(0.8))
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(brandBlue)
    }

    // MARK: - Error banner

    private func errorBanner(_ message: String) -> some View {
        HStack {
            Text(message)
                .font(.caption)
                .foregroundColor(Color(red: 0.75, green: 0.22, blue: 0.17))
            Spacer()
            Button("✕") { vm.dismissError() }
                .foregroundColor(Color(red: 0.75, green: 0.22, blue: 0.17))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color(red: 0.99, green: 0.91, blue: 0.91))
    }
}
