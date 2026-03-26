import SwiftUI

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct SidebarView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ZStack(alignment: .leading) {
            // Dim background tap to close
            if vm.sidebarOpen {
                Color.black.opacity(0.4)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation(.easeInOut(duration: 0.25)) { vm.sidebarOpen = false } }
                    .transition(.opacity)
            }

            // Drawer panel
            if vm.sidebarOpen {
                drawerPanel
                    .transition(.move(edge: .leading))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: vm.sidebarOpen)
    }

    private var drawerPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header row
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("roozbeh.realtor")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundColor(.white)
                    Text("DRE#: 02225608")
                        .font(.system(size: 11))
                        .foregroundColor(.white.opacity(0.45))
                }
                Spacer()
                Button(action: { withAnimation { vm.sidebarOpen = false } }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white.opacity(0.7))
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 60)
            .padding(.bottom, 16)

            // New chat button
            Button(action: { vm.startNewChat() }) {
                HStack(spacing: 10) {
                    Image(systemName: "square.and.pencil")
                    Text("New Conversation")
                        .font(.system(size: 15))
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .background(Color.white.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 12)
            }

            Divider()
                .background(Color.white.opacity(0.15))
                .padding(.vertical, 12)

            // Session list
            if vm.currentUser == nil {
                VStack(spacing: 12) {
                    Image(systemName: "clock.arrow.circlepath")
                        .font(.system(size: 28))
                        .foregroundColor(.white.opacity(0.35))
                    Text("Sign in to save and access your previous conversations")
                        .font(.system(size: 14))
                        .foregroundColor(.white.opacity(0.5))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 30)
            } else if vm.isLoadingSessions {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 20)
            } else if vm.sessions.isEmpty {
                Text("No previous conversations")
                    .font(.system(size: 14))
                    .foregroundColor(.white.opacity(0.45))
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.top, 20)
            } else {
                List {
                    ForEach(vm.sessions) { session in
                        sessionRow(session)
                            .listRowBackground(Color.clear)
                            .listRowInsets(EdgeInsets(top: 2, leading: 12, bottom: 2, trailing: 12))
                            .listRowSeparator(.hidden)
                    }
                    .onDelete { indexSet in
                        indexSet.forEach { vm.deleteSession(vm.sessions[$0]) }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
            }

            Spacer()

            // User footer
            Divider().background(Color.white.opacity(0.15))
            userFooter
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
        }
        .frame(width: 300)
        .background(brandBlue)
        .ignoresSafeArea(edges: .vertical)
    }

    private func sessionRow(_ session: ChatSession) -> some View {
        Button(action: { vm.restoreSession(session) }) {
            Text(session.title.isEmpty ? "Conversation" : session.title)
                .font(.system(size: 14))
                .foregroundColor(.white.opacity(0.85))
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.white.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    @ViewBuilder
    private var userFooter: some View {
        if let user = vm.currentUser {
            HStack(spacing: 10) {
                Circle()
                    .fill(Color.white.opacity(0.2))
                    .frame(width: 36, height: 36)
                    .overlay(
                        Text(String(user.name.prefix(1)).uppercased())
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(.white)
                    )
                VStack(alignment: .leading, spacing: 2) {
                    Text(user.name)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.white)
                    Text(user.email)
                        .font(.system(size: 11))
                        .foregroundColor(.white.opacity(0.55))
                        .lineLimit(1)
                }
                Spacer()
                Button(action: { vm.signOut() }) {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                        .font(.system(size: 15))
                        .foregroundColor(.white.opacity(0.6))
                }
            }
        } else {
            Button(action: { vm.showingLogin = true; vm.sidebarOpen = false }) {
                HStack(spacing: 10) {
                    Image(systemName: "person.circle")
                        .font(.system(size: 22))
                        .foregroundColor(.white.opacity(0.7))
                    Text("Sign in")
                        .font(.system(size: 15))
                        .foregroundColor(.white)
                }
            }
        }
    }
}
