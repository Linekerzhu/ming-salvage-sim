import Foundation

struct EnvironmentSeedFile<Record: Decodable>: Decodable {
    let schemaVersion: Int
    let generatedAt: String
    let records: [Record]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case records
    }
}

struct EnvironmentDatabaseManifest: Decodable {
    let schemaVersion: Int
    let generatedAt: String
    let database: String
    let displayName: String
    let boundary: String
    let referenceSources: [EnvironmentReferenceSource]?
    let modules: [EnvironmentDatabaseModule]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case database
        case displayName = "display_name"
        case boundary
        case referenceSources = "reference_sources"
        case modules
    }
}

struct EnvironmentReferenceSource: Decodable, Identifiable {
    let label: String
    let url: String
    let usePolicy: String?

    var id: String { label }

    enum CodingKeys: String, CodingKey {
        case label
        case url
        case usePolicy = "use_policy"
    }
}

struct EnvironmentDatabaseModule: Decodable, Identifiable {
    let module: String
    let filename: String
    let recordCount: Int

    var id: String { module }

    enum CodingKeys: String, CodingKey {
        case module
        case filename
        case recordCount = "record_count"
    }
}

struct MingRankSystemRecord: Decodable, Identifiable {
    let rankCode: String
    let label: String
    let rankSystem: String
    let grade: Int?
    let polarity: String?
    let sortOrder: Int
    let appliesToContexts: [String]
    let sourceURL: String?
    let sourceNote: String?
    let confidence: String?
    let reviewStatus: String?

    var id: String { rankCode }

    enum CodingKeys: String, CodingKey {
        case rankCode = "rank_code"
        case label
        case rankSystem = "rank_system"
        case grade
        case polarity
        case sortOrder = "sort_order"
        case appliesToContexts = "applies_to_contexts"
        case sourceURL = "source_url"
        case sourceNote = "source_note"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct MingInstitutionRecord: Decodable, Identifiable {
    let institutionID: String
    let name: String
    let layer: String
    let institutionFamily: String
    let parentInstitutionID: String?
    let source: String?
    let sourceURL: String?
    let confidence: String?
    let reviewStatus: String?
    let notes: String?

    var id: String { institutionID }

    enum CodingKeys: String, CodingKey {
        case institutionID = "institution_id"
        case name
        case layer
        case institutionFamily = "institution_family"
        case parentInstitutionID = "parent_institution_id"
        case source
        case sourceURL = "source_url"
        case confidence
        case reviewStatus = "review_status"
        case notes
    }
}

struct MingOfficePostRecord: Decodable, Identifiable {
    let officePostID: String
    let canonicalTitle: String
    let titleVariants: [String]
    let institutionID: String
    let rankContext: String
    let defaultRankCode: String?
    let rankApplicability: String
    let titleOrder: Int?
    let capacityPolicy: String
    let officeFamily: String
    let jurisdictionScope: String
    let source: String?
    let sourceURL: String?
    let sourceNote: String?
    let confidence: String?
    let reviewStatus: String?
    let oldOfficePatterns: [String]?

    var id: String { officePostID }

    enum CodingKeys: String, CodingKey {
        case officePostID = "office_post_id"
        case canonicalTitle = "canonical_title"
        case titleVariants = "title_variants"
        case institutionID = "institution_id"
        case rankContext = "rank_context"
        case defaultRankCode = "default_rank_code"
        case rankApplicability = "rank_applicability"
        case titleOrder = "title_order"
        case capacityPolicy = "capacity_policy"
        case officeFamily = "office_family"
        case jurisdictionScope = "jurisdiction_scope"
        case source
        case sourceURL = "source_url"
        case sourceNote = "source_note"
        case confidence
        case reviewStatus = "review_status"
        case oldOfficePatterns = "old_office_patterns"
    }
}

struct OldOfficeIndexRecord: Decodable, Identifiable {
    let oldOfficeIndexID: String
    let oldOfficeText: String
    let oldOfficePart: String
    let officeType: String
    let matchedOfficePostID: String?
    let matchStatus: String
    let source: String?
    let reviewStatus: String?

    var id: String { oldOfficeIndexID }

    enum CodingKeys: String, CodingKey {
        case oldOfficeIndexID = "old_office_index_id"
        case oldOfficeText = "old_office_text"
        case oldOfficePart = "old_office_part"
        case officeType = "office_type"
        case matchedOfficePostID = "matched_office_post_id"
        case matchStatus = "match_status"
        case source
        case reviewStatus = "review_status"
    }
}

struct AdministrativeRegionRecord: Decodable, Identifiable {
    let regionID: String
    let name: String
    let regionType: String
    let parentRegionID: String?
    let powerID: String
    let source: String?
    let sourceURL: String?
    let confidence: String?
    let reviewStatus: String?
    let notes: String?
    let legacyLocationCodes: [String]?
    let sourceOfficeExamples: [String]?

    var id: String { regionID }

    enum CodingKeys: String, CodingKey {
        case regionID = "region_id"
        case name
        case regionType = "region_type"
        case parentRegionID = "parent_region_id"
        case powerID = "power_id"
        case source
        case sourceURL = "source_url"
        case confidence
        case reviewStatus = "review_status"
        case notes
        case legacyLocationCodes = "legacy_location_codes"
        case sourceOfficeExamples = "source_office_examples"
    }
}

struct FormalAdministrativeDivisionRecord: Decodable, Identifiable {
    let regionID: String
    let name: String
    let level: Int
    let regionType: String
    let parentRegionID: String?
    let topLevelRegionID: String?
    let sortOrder: Int
    let isFormalCivilAdministration: Bool
    let reviewStatus: String?
    let notes: String?

    var id: String { regionID }

    enum CodingKeys: String, CodingKey {
        case regionID = "region_id"
        case name
        case level
        case regionType = "region_type"
        case parentRegionID = "parent_region_id"
        case topLevelRegionID = "top_level_region_id"
        case sortOrder = "sort_order"
        case isFormalCivilAdministration = "is_formal_civil_administration"
        case reviewStatus = "review_status"
        case notes
    }
}

struct LocationAliasRecord: Decodable, Identifiable {
    let aliasID: String
    let alias: String
    let aliasType: String
    let regionID: String
    let source: String?
    let confidence: String?
    let reviewStatus: String?

    var id: String { aliasID }

    enum CodingKeys: String, CodingKey {
        case aliasID = "alias_id"
        case alias
        case aliasType = "alias_type"
        case regionID = "region_id"
        case source
        case confidence
        case reviewStatus = "review_status"
    }
}

struct MingEunuchAgencyRecord: Decodable, Identifiable {
    let agencyID: String
    let name: String
    let agencyGroup: String
    let institutionID: String
    let parentInstitutionID: String?
    let institutionFamily: String
    let duties: [String]
    let staffStructure: [String]
    let politicalWeight: String
    let isTwentyFourYamen: Bool
    let sortOrder: Int
    let source: String?
    let sourcePath: String?
    let sourceNote: String?
    let confidence: String?
    let reviewStatus: String?

    var id: String { agencyID }

    enum CodingKeys: String, CodingKey {
        case agencyID = "agency_id"
        case name
        case agencyGroup = "agency_group"
        case institutionID = "institution_id"
        case parentInstitutionID = "parent_institution_id"
        case institutionFamily = "institution_family"
        case duties
        case staffStructure = "staff_structure"
        case politicalWeight = "political_weight"
        case isTwentyFourYamen = "is_twenty_four_yamen"
        case sortOrder = "sort_order"
        case source
        case sourcePath = "source_path"
        case sourceNote = "source_note"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct MingEunuchAttireMarkerRecord: Decodable, Identifiable {
    let markerID: String
    let displayTier: Int
    let rankLabel: String
    let rankCode: String?
    let roleScope: String
    let robe: String
    let hat: String
    let belt: String
    let badgeOrToken: String
    let allowedContext: String
    let promptUsePolicy: String?
    let sourceNote: String?
    let source: String?
    let sourcePath: String?
    let confidence: String?
    let reviewStatus: String?

    var id: String { markerID }

    enum CodingKeys: String, CodingKey {
        case markerID = "marker_id"
        case displayTier = "display_tier"
        case rankLabel = "rank_label"
        case rankCode = "rank_code"
        case roleScope = "role_scope"
        case robe
        case hat
        case belt
        case badgeOrToken = "badge_or_token"
        case allowedContext = "allowed_context"
        case promptUsePolicy = "prompt_use_policy"
        case sourceNote = "source_note"
        case source
        case sourcePath = "source_path"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct EnvironmentDatabase {
    let manifest: EnvironmentDatabaseManifest
    let rankSystem: [MingRankSystemRecord]
    let institutions: [MingInstitutionRecord]
    let officePosts: [MingOfficePostRecord]
    let administrativeRegions: [AdministrativeRegionRecord]
    let formalAdministrativeDivisions1628: [FormalAdministrativeDivisionRecord]
    let locationAliases: [LocationAliasRecord]
    let eunuchAgencies: [MingEunuchAgencyRecord]
    let eunuchAttireMarkers: [MingEunuchAttireMarkerRecord]

    var officePostsByID: [String: MingOfficePostRecord] {
        Dictionary(uniqueKeysWithValues: officePosts.map { ($0.officePostID, $0) })
    }

    var regionsByID: [String: AdministrativeRegionRecord] {
        Dictionary(uniqueKeysWithValues: administrativeRegions.map { ($0.regionID, $0) })
    }
}

enum EnvironmentDatabaseLoadError: Error, LocalizedError {
    case missingResource(String)

    var errorDescription: String? {
        switch self {
        case .missingResource(let name):
            return "Missing environment database resource: \(name).json"
        }
    }
}

final class EnvironmentDatabaseStore {
    let database: EnvironmentDatabase

    init(bundle: Bundle = .main) throws {
        let manifest = try Self.decodeManifest(bundle: bundle)
        database = EnvironmentDatabase(
            manifest: manifest,
            rankSystem: try Self.decodeSeed("ming_rank_system_seed", bundle: bundle),
            institutions: try Self.decodeSeed("ming_institutions_seed", bundle: bundle),
            officePosts: try Self.decodeSeed("ming_office_posts_seed", bundle: bundle),
            administrativeRegions: try Self.decodeSeed("administrative_regions_seed", bundle: bundle),
            formalAdministrativeDivisions1628: try Self.decodeSeed("formal_administrative_divisions_1628_seed", bundle: bundle),
            locationAliases: try Self.decodeSeed("location_aliases_seed", bundle: bundle),
            eunuchAgencies: try Self.decodeSeed("ming_eunuch_agencies_seed", bundle: bundle),
            eunuchAttireMarkers: try Self.decodeSeed("ming_eunuch_attire_markers_seed", bundle: bundle)
        )
    }

    private static func decodeSeed<Record: Decodable>(_ resource: String, bundle: Bundle) throws -> [Record] {
        let url = try resourceURL(resource, bundle: bundle)
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(EnvironmentSeedFile<Record>.self, from: data).records
    }

    private static func decodeManifest(bundle: Bundle) throws -> EnvironmentDatabaseManifest {
        let url = try resourceURL("environment_database_manifest", bundle: bundle)
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(EnvironmentDatabaseManifest.self, from: data)
    }

    private static func resourceURL(_ resource: String, bundle: Bundle) throws -> URL {
        if let url = bundle.url(forResource: resource, withExtension: "json", subdirectory: "EnvironmentDatabase") {
            return url
        }
        if let url = bundle.url(forResource: resource, withExtension: "json") {
            return url
        }
        throw EnvironmentDatabaseLoadError.missingResource(resource)
    }
}
