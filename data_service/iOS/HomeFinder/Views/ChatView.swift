import SwiftUI

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct ChatView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
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
                        // Invisible anchor for scroll-to-bottom
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(.vertical, 16)
                }
                .onChange(of: vm.messages.count) { _ in
                    // Brief delay lets SwiftUI finish layout before scrolling
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                        withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                    }
                }
            }

            InputBarView()
        }
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .background(Color(.systemGroupedBackground))
    }

    // MARK: - Subviews

    private var headerView: some View {
        HStack(spacing: 10) {
            Text("🏠").font(.title2)
            VStack(alignment: .leading, spacing: 1) {
                Text("roozbeh.realtor")
                    .font(.headline).fontWeight(.bold)
                    .foregroundColor(.white)
                Text("Bay Area Home Finder")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.8))
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(brandBlue)
    }

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
