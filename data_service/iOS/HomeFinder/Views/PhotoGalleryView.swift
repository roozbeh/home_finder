import SwiftUI

struct PhotoGalleryView: View {
    let photos: [String]
    let title: String
    @Environment(\.dismiss) private var dismiss
    @State private var currentIndex = 0

    var body: some View {
        ZStack(alignment: .top) {
            Color.black.ignoresSafeArea()

            TabView(selection: $currentIndex) {
                ForEach(photos.indices, id: \.self) { i in
                    AsyncImage(url: URL(string: photos[i])) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .scaledToFit()
                        case .failure:
                            Image(systemName: "photo")
                                .font(.system(size: 48))
                                .foregroundColor(.gray)
                        default:
                            ProgressView().tint(.white)
                        }
                    }
                    .tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .ignoresSafeArea()

            // Top bar
            VStack(spacing: 0) {
                HStack {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(.white)
                            .padding(10)
                            .background(Color.black.opacity(0.5))
                            .clipShape(Circle())
                    }
                    Spacer()
                    Text("\(currentIndex + 1) / \(photos.count)")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(Color.black.opacity(0.5))
                        .clipShape(Capsule())
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)

                if !title.isEmpty {
                    Text(title)
                        .font(.system(size: 13))
                        .foregroundColor(.white.opacity(0.75))
                        .lineLimit(1)
                        .padding(.top, 8)
                }
            }
        }
    }
}
