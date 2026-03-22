import SwiftUI
import UIKit

private let brandBlue   = Color(red: 0.035, green: 0.165, blue: 0.337)
// Adaptive colors — automatically flip for dark mode
private let bubbleBot   = Color(.secondarySystemBackground)
private let bubbleBotFG = Color(.label)

struct MessageBubbleView: View {
    let message: ChatMessage

    var body: some View {
        VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 0) {
            HStack(alignment: .bottom, spacing: 8) {
                if message.role != .user {
                    avatarView
                }

                bubbleContent
                    .frame(maxWidth: UIScreen.main.bounds.width * 0.72,
                           alignment: message.role == .user ? .trailing : .leading)

                if message.role == .user {
                    Spacer(minLength: 0)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 4)

            if !message.listings.isEmpty {
                ListingsScrollView(listings: message.listings)
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
    }

    // MARK: - Subviews

    private var avatarView: some View {
        Image(systemName: "house.fill")
            .font(.system(size: 15))
            .foregroundColor(.white)
            .frame(width: 36, height: 36)
            .background(brandBlue)
            .clipShape(Circle())
    }

    @ViewBuilder
    private var bubbleContent: some View {
        switch message.role {
        case .typing:
            if message.text.isEmpty {
                TypingIndicatorView()
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(bubbleBot)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
            } else {
                Text(message.text)
                    .font(.system(size: 15))
                    .italic()
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(bubbleBot)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
            }

        case .user:
            Text(message.text)
                .font(.system(size: 15))
                .lineSpacing(3)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(brandBlue)
                .clipShape(RoundedRectangle(cornerRadius: 16))

        case .assistant:
            Text(message.text)
                .font(.system(size: 15))
                .lineSpacing(3)
                .foregroundColor(bubbleBotFG)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(bubbleBot)
                .clipShape(RoundedRectangle(cornerRadius: 16))
        }
    }
}
