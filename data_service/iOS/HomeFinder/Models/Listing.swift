import Foundation

struct Listing: Codable, Identifiable {
    let listingId: String?
    let streetAddress: String?
    let city: String?
    let listPrice: Double?
    let bedroomsTotal: Int?
    let bathroomsFull: Int?
    let bathsDisplay: String?
    let sqft: Double?
    let mlsStatus: String?

    // Identifiable — fall back to a random UUID if LISTING_ID is missing
    var id: String { listingId ?? UUID().uuidString }

    enum CodingKeys: String, CodingKey {
        case listingId     = "LISTING_ID"
        case streetAddress = "STREET_ADDRESS"
        case city          = "CITY"
        case listPrice     = "LIST_PRICE"
        case bedroomsTotal = "BEDROOMS_TOTAL"
        case bathroomsFull = "BATHROOMS_FULL"
        case bathsDisplay  = "BATHS_DISPLAY"
        case sqft          = "SQFT"
        case mlsStatus     = "MLS_STATUS"
    }

    var formattedPrice: String {
        guard let price = listPrice else { return "N/A" }
        let fmt = NumberFormatter()
        fmt.numberStyle = .currency
        fmt.maximumFractionDigits = 0
        fmt.currencySymbol = "$"
        return fmt.string(from: NSNumber(value: price)) ?? "N/A"
    }

    /// "FOSTERCITY" → "Fostercity",  "SAN MATEO" → "San Mateo"
    var formattedCity: String {
        guard let city else { return "" }
        return city.capitalized
    }

    var displayBaths: String {
        bathsDisplay ?? (bathroomsFull.map { "\($0)" } ?? "?")
    }
}
