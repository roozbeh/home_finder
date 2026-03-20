import SwiftUI

struct ListingsScrollView: View {
    let listings: [Listing]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 14) {
                ForEach(listings) { listing in
                    ListingCardView(listing: listing)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
    }
}
