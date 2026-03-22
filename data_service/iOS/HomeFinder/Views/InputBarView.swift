import SwiftUI

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct InputBarView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(alignment: .bottom, spacing: 10) {
                ZStack(alignment: .topLeading) {
                    // Placeholder
                    if text.isEmpty {
                        Text("Tell me what you're looking for…")
                            .font(.system(size: 15))
                            .foregroundColor(Color(.placeholderText))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .allowsHitTesting(false)
                    }

                    TextEditor(text: $text)
                        .font(.system(size: 15))
                        .frame(minHeight: 36, maxHeight: 120)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .scrollContentBackground(.hidden)
                        .focused($focused)
                        .disabled(vm.isLoading)
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 4)
                .background(Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 20))

                Button(action: sendIfValid) {
                    Image(systemName: "paperplane.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 40, height: 40)
                        .background(canSend ? brandBlue : Color(.systemGray3))
                        .clipShape(Circle())
                }
                .disabled(!canSend)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(Color(.systemBackground))
        }
    }

    private var canSend: Bool {
        !vm.isLoading && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func sendIfValid() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        vm.send(text: trimmed)
        text = ""
        focused = false
    }
}
