import SwiftUI

struct TypingIndicatorView: View {
    @State private var animate = false

    var body: some View {
        HStack(spacing: 5) {
            dot(delay: 0.0)
            dot(delay: 0.2)
            dot(delay: 0.4)
        }
        .onAppear { animate = true }
        .onDisappear { animate = false }
    }

    private func dot(delay: Double) -> some View {
        Circle()
            .fill(Color(white: 0.6))
            .frame(width: 8, height: 8)
            .offset(y: animate ? -5 : 0)
            .animation(
                .easeInOut(duration: 0.5)
                    .repeatForever()
                    .delay(delay),
                value: animate
            )
    }
}
