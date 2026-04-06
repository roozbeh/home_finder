import SwiftUI

private let brandBlue = Color(red: 0.035, green: 0.165, blue: 0.337)

struct ListingCardView: View {
    let listing: Listing
    @EnvironmentObject var vm: ChatViewModel
    @State private var vote: FeedbackVote?
    @State private var showingGallery = false
    @State private var showingLoginPrompt = false

    private var galleryPhotos: [String] {
        listing.photos.isEmpty
            ? ([listing.thumbphoto].compactMap { $0 })
            : listing.photos
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            photoHeader

            VStack(alignment: .leading, spacing: 6) {
                Text(listing.formattedPrice)
                    .font(.system(size: 18, weight: .bold))
                    .foregroundColor(brandBlue)

                Text([listing.streetAddress, listing.formattedCity]
                    .compactMap { $0 }.joined(separator: ", "))
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                    .lineLimit(2)

                HStack(spacing: 10) {
                    if let beds = listing.bedroomsTotal {
                        statLabel("bed.double.fill", "\(beds) bd")
                    }
                    statLabel("shower.fill", "\(listing.displayBaths) ba")
                    if let sqft = listing.sqft, sqft > 0 {
                        statLabel("ruler.fill", "\(Int(sqft)) sqft")
                    }
                }

                HStack(spacing: 8) {
                    feedbackButton(.good, label: "Good",  icon: "hand.thumbsup.fill",
                                   activeBackground: Color(red: 0.82, green: 0.96, blue: 0.88),
                                   activeText: .green)
                    feedbackButton(.bad,  label: "Pass",  icon: "hand.thumbsdown.fill",
                                   activeBackground: Color(red: 0.99, green: 0.91, blue: 0.91),
                                   activeText: .red)
                }

                if let id = listing.listingId,
                   let url = URL(string: "\(AppConfig.baseURL)/listing/\(id)") {
                    HStack {
                        Link("View full details →", destination: url)
                            .font(.system(size: 12))
                            .foregroundColor(Color(UIColor.tertiaryLabel))
                        Spacer()
                        let shareText = "\(listing.formattedPrice) · \([listing.streetAddress, listing.formattedCity].compactMap { $0 }.joined(separator: ", "))"
                        ShareLink(item: url, subject: Text(listing.streetAddress ?? "Home for Sale"), message: Text(shareText)) {
                            Image(systemName: "square.and.arrow.up")
                                .font(.system(size: 13))
                                .foregroundColor(Color(UIColor.tertiaryLabel))
                        }
                    }
                }
            }
            .padding(12)
        }
        .frame(width: 260)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .shadow(color: .black.opacity(0.1), radius: 6, x: 0, y: 2)
        .sheet(isPresented: $showingGallery) {
            let addr = [listing.streetAddress, listing.formattedCity]
                .compactMap { $0 }.joined(separator: ", ")
            PhotoGalleryView(photos: galleryPhotos, title: addr)
        }
        .alert("Sign In to Save Favorites", isPresented: $showingLoginPrompt) {
            Button("Sign In") {
                vm.showingLogin = true
            }
            Button("Not Now", role: .cancel) {}
        } message: {
            Text("Sign in to save your favorite listings and access them anytime.")
        }
    }

    // MARK: - Subviews

    private var photoHeader: some View {
        ZStack(alignment: .topTrailing) {
            // Photo or gradient placeholder
            if let thumb = listing.thumbphoto, let url = URL(string: thumb) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        gradientPlaceholder
                    case .empty:
                        gradientPlaceholder
                    @unknown default:
                        gradientPlaceholder
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
            } else {
                gradientPlaceholder
            }

            // Overlays: status badge top-right, photo count bottom-right
            VStack {
                if let status = listing.mlsStatus {
                    statusBadge(status).padding(8)
                }
                Spacer()
                if !galleryPhotos.isEmpty {
                    Label("\(galleryPhotos.count)", systemImage: "photo.on.rectangle")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.black.opacity(0.5))
                        .clipShape(Capsule())
                        .padding(8)
                }
            }
        }
        .frame(height: 140)
        .clipped()
        .contentShape(Rectangle())
        .onTapGesture {
            if !galleryPhotos.isEmpty { showingGallery = true }
        }
    }

    private var gradientPlaceholder: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.1, green: 0.29, blue: 0.54), brandBlue],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            Image(systemName: "house.fill")
                .font(.system(size: 40))
                .foregroundColor(.white.opacity(0.25))
        }
    }

    private func statusBadge(_ status: String) -> some View {
        Text(status)
            .font(.system(size: 11, weight: .bold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(badgeColor(for: status))
            .foregroundColor(badgeTextColor(for: status))
            .clipShape(Capsule())
    }

    private func statLabel(_ icon: String, _ text: String) -> some View {
        HStack(spacing: 3) {
            Image(systemName: icon)
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            Text(text).font(.system(size: 12)).foregroundColor(.secondary)
        }
    }

    private func feedbackButton(
        _ voteType: FeedbackVote,
        label: String,
        icon: String,
        activeBackground: Color,
        activeText: Color
    ) -> some View {
        let isActive = vote == voteType
        return Button {
            guard vm.currentUser != nil else {
                showingLoginPrompt = true
                return
            }
            vote = voteType
            if let id = listing.listingId {
                vm.postFeedback(listingId: id, vote: voteType)
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 12))
                Text(label).font(.system(size: 13))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(isActive ? activeBackground : Color(.systemGray6))
            .foregroundColor(isActive ? activeText : .primary)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isActive ? activeText : Color(.systemGray4), lineWidth: 1)
            )
        }
    }

    // MARK: - Helpers

    private func badgeColor(for status: String) -> Color {
        switch status {
        case "ACTV":  return Color(red: 0.098, green: 0.529, blue: 0.329)
        case "NEW":   return Color(red: 0.051, green: 0.431, blue: 0.992)
        case "PCH":   return Color(red: 0.051, green: 0.792, blue: 0.941)
        case "CS":    return Color(red: 1.0,   green: 0.757, blue: 0.027)
        case "AC":    return Color(red: 0.4,   green: 0.063, blue: 0.945)
        default:      return Color(.systemGray)
        }
    }

    private func badgeTextColor(for status: String) -> Color {
        switch status {
        case "PCH", "CS": return .black
        default:          return .white
        }
    }
}
