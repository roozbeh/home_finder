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
    let thumbphoto: String?
    let photos: [String]

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
        case thumbphoto    = "thumbphoto"
        case photos        = "photos"
    }

    init(from decoder: Decoder) throws {
        let c          = try decoder.container(keyedBy: CodingKeys.self)
        listingId      = try c.decodeIfPresent(String.self,  forKey: .listingId)
        streetAddress  = try c.decodeIfPresent(String.self,  forKey: .streetAddress)
        city           = try c.decodeIfPresent(String.self,  forKey: .city)
        listPrice      = try c.decodeIfPresent(Double.self,  forKey: .listPrice)
        bedroomsTotal  = try c.decodeIfPresent(Int.self,     forKey: .bedroomsTotal)
        bathroomsFull  = try c.decodeIfPresent(Int.self,     forKey: .bathroomsFull)
        bathsDisplay   = try c.decodeIfPresent(String.self,  forKey: .bathsDisplay)
        sqft           = try c.decodeIfPresent(Double.self,  forKey: .sqft)
        mlsStatus      = try c.decodeIfPresent(String.self,  forKey: .mlsStatus)
        thumbphoto     = try c.decodeIfPresent(String.self,  forKey: .thumbphoto)
        photos         = (try? c.decode([String].self, forKey: .photos)) ?? []
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
