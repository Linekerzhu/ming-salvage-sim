import Foundation

struct NPCSeedFile<Record: Decodable>: Decodable {
    let schemaVersion: Int
    let generatedAt: String
    let records: [Record]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case records
    }
}

struct NPCDatabaseManifest: Decodable {
    let schemaVersion: Int
    let generatedAt: String
    let modules: [NPCDatabaseModule]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case modules
    }
}

struct NPCDatabaseModule: Decodable, Identifiable {
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

struct NPCRankSystemCatalogRecord: Decodable, Identifiable {
    let rankCode: String
    let label: String
    let grade: Int?
    let polarity: String?
    let sortOrder: Int
    let appliesToContexts: [String]
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?
    let notes: String?

    var id: String { rankCode }

    enum CodingKeys: String, CodingKey {
        case rankCode = "rank_code"
        case label
        case grade
        case polarity
        case sortOrder = "sort_order"
        case appliesToContexts = "applies_to_contexts"
        case source
        case confidence
        case reviewStatus = "review_status"
        case notes
    }
}

struct NPCStatusCatalogRecord: Decodable, Identifiable {
    let statusCode: NPCStatusCode
    let label: String
    let summonableByDefault: Bool
    let appointableByDefault: Bool
    let description: String
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { statusCode.rawValue }

    enum CodingKeys: String, CodingKey {
        case statusCode = "status_code"
        case label
        case summonableByDefault = "summonable_by_default"
        case appointableByDefault = "appointable_by_default"
        case description
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCInstitutionCatalogRecord: Decodable, Identifiable {
    let institutionID: String
    let label: String
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { institutionID }

    enum CodingKeys: String, CodingKey {
        case institutionID = "institution_id"
        case label
        case source
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCOfficeCatalogRecord: Decodable, Identifiable {
    let officeCatalogID: String
    let institutionID: String
    let officeSlotID: String
    let environmentOfficePostID: String?
    let environmentOfficeCanonicalTitle: String?
    let environmentOfficeMatchStatus: String?
    let officeCapacityPolicy: String
    let canonicalTitle: String
    let titleVariants: [String]
    let rankContext: RankContext
    let defaultRankCode: OfficialRankCode?
    let rankApplicability: RankApplicability
    let defaultAppointmentKinds: [AppointmentKind]
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?
    let notes: String?

    var id: String { officeCatalogID }

    enum CodingKeys: String, CodingKey {
        case officeCatalogID = "office_catalog_id"
        case institutionID = "institution_id"
        case officeSlotID = "office_slot_id"
        case environmentOfficePostID = "environment_office_post_id"
        case environmentOfficeCanonicalTitle = "environment_office_canonical_title"
        case environmentOfficeMatchStatus = "environment_office_match_status"
        case officeCapacityPolicy = "office_capacity_policy"
        case canonicalTitle = "canonical_title"
        case titleVariants = "title_variants"
        case rankContext = "rank_context"
        case defaultRankCode = "default_rank_code"
        case rankApplicability = "rank_applicability"
        case defaultAppointmentKinds = "default_appointment_kinds"
        case source
        case confidence
        case reviewStatus = "review_status"
        case notes
    }
}

struct NPCCulturalLiteracyCatalogRecord: Decodable, Identifiable {
    let level: Int
    let label: String
    let anchor: String
    let scaleMin: Int
    let scaleMax: Int
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: Int { level }

    enum CodingKeys: String, CodingKey {
        case level
        case label
        case anchor
        case scaleMin = "scale_min"
        case scaleMax = "scale_max"
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCSkillAnchorLevel: Decodable, Identifiable {
    let level: Int
    let label: String
    let description: String

    var id: Int { level }
}

struct NPCSkillAnchorCatalogRecord: Decodable, Identifiable {
    let skillID: String
    let skillName: String
    let skillType: String
    let linkedDimensionID: String?
    let linkedCapabilityCategory: String?
    let scaleMin: Int
    let scaleMax: Int
    let anchors: [NPCSkillAnchorLevel]
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { skillID }

    enum CodingKeys: String, CodingKey {
        case skillID = "skill_id"
        case skillName = "skill_name"
        case skillType = "skill_type"
        case linkedDimensionID = "linked_dimension_id"
        case linkedCapabilityCategory = "linked_capability_category"
        case scaleMin = "scale_min"
        case scaleMax = "scale_max"
        case anchors
        case source
        case reviewStatus = "review_status"
    }
}

enum SexCategory: String, Decodable {
    case male
    case female
    case eunuch
    case unknown
}

enum NPCRecordSource: String, Decodable {
    case oldStaticSeed = "old_static_seed"
    case historical
    case userConfirmed = "user_confirmed"
    case generatedCandidate = "generated_candidate"
}

enum NPCReviewStatus: String, Decodable {
    case reviewed
    case needsReview = "needs_review"
    case unknownNeedsReview = "unknown_needs_review"
}

enum NPCStatusCode: String, Decodable {
    case activeInOffice = "active_in_office"
    case activeUnassigned = "active_unassigned"
    case idleHome = "idle_home"
    case candidate
    case offstage
    case suspended
    case dismissed
    case retired
    case imprisoned
    case exiled
    case dead
}

enum RankContext: String, Decodable {
    case outerCivil = "outer_civil"
    case outerMilitary = "outer_military"
    case innerEunuch = "inner_eunuch"
    case femaleOfficial = "female_official"
    case haremTitle = "harem_title"
    case nobility
    case civilian
    case foreignTitle = "foreign_title"
    case rebelTitle = "rebel_title"
}

enum RankApplicability: String, Decodable {
    case ranked
    case titleOrderOnly = "title_order_only"
    case militaryCommandVariable = "military_command_variable"
    case missionOrDispatchVariable = "mission_or_dispatch_variable"
    case innerEunuchOrder = "inner_eunuch_order"
    case civilianUnranked = "civilian_unranked"
    case foreignNotMing = "foreign_not_ming"
    case rebelOrUnrecognizedTitle = "rebel_or_unrecognized_title"
    case unofficialUnranked = "unofficial_unranked"
    case unknown
}

enum OfficialRankCode: String, Decodable {
    case zheng1 = "zheng_1"
    case cong1 = "cong_1"
    case zheng2 = "zheng_2"
    case cong2 = "cong_2"
    case zheng3 = "zheng_3"
    case cong3 = "cong_3"
    case zheng4 = "zheng_4"
    case cong4 = "cong_4"
    case zheng5 = "zheng_5"
    case cong5 = "cong_5"
    case zheng6 = "zheng_6"
    case cong6 = "cong_6"
    case zheng7 = "zheng_7"
    case cong7 = "cong_7"
    case zheng8 = "zheng_8"
    case cong8 = "cong_8"
    case zheng9 = "zheng_9"
    case cong9 = "cong_9"
}

enum AppointmentKind: String, Decodable {
    case substantive
    case concurrent
    case acting
    case mission
    case honorary
    case former
}

enum RelationshipKind: String, Decodable {
    case kinshipOrHousehold = "kinship_or_household"
    case examCohort = "exam_cohort"
    case nativePlaceCohort = "native_place_cohort"
    case mentorLine = "mentor_line"
    case patronClient = "patron_client"
    case colleague
    case affiliationPeer = "affiliation_peer"
    case rivalry
    case narrativeTie = "narrative_tie"
}

struct NPCHistoricalDeath: Decodable {
    let year: Int?
    let month: Int?
}

struct NPCPlaceFact: Decodable {
    let province: String?
    let prefecture: String?
    let county: String?
    let confidence: String?
    let sourceNote: String?

    enum CodingKeys: String, CodingKey {
        case province
        case prefecture
        case county
        case confidence
        case sourceNote = "source_note"
    }
}

struct NPCLocationFact: Decodable {
    let place: String
    let kind: String
    let confidence: String?
    let source: String?
}

struct NPCLegacyStats: Decodable {
    let loyalty: Int
    let ability: Int
    let integrity: Int
    let courage: Int
    let force: Int
    let wisdom: Int
    let charm: Int
    let luck: Int
    let cultivation: Int
    let hp: Int
    let maxHP: Int
    let exp: Int
    let level: Int
    let usePolicy: String?

    enum CodingKeys: String, CodingKey {
        case loyalty
        case ability
        case integrity
        case courage
        case force
        case wisdom
        case charm
        case luck
        case cultivation
        case hp
        case maxHP = "max_hp"
        case exp
        case level
        case usePolicy = "use_policy"
    }
}

struct NPCCoreRecord: Decodable, Identifiable {
    let npcID: String
    let legacySequenceID: String?
    let idPolicy: String?
    let canonicalName: String
    let aliases: [String]
    let sexCategory: SexCategory
    let birthYear: Int?
    let historicalDeath: NPCHistoricalDeath
    let nativePlace: NPCPlaceFact
    let powerID: String
    let identityType: String
    let recordSource: NPCRecordSource?
    let reviewStatus: NPCReviewStatus?
    let sourceNote: String?
    let legacyStats: NPCLegacyStats?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case legacySequenceID = "legacy_sequence_id"
        case idPolicy = "id_policy"
        case canonicalName = "canonical_name"
        case aliases
        case sexCategory = "sex_category"
        case birthYear = "birth_year"
        case historicalDeath = "historical_death"
        case nativePlace = "native_place"
        case powerID = "power_id"
        case identityType = "identity_type"
        case recordSource = "record_source"
        case reviewStatus = "review_status"
        case sourceNote = "source_note"
        case legacyStats = "legacy_stats"
    }
}

struct NPCCapabilityFact: Decodable, Identifiable {
    let capabilityID: String
    let label: String
    let category: String
    let evidenceKind: String?
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?
    let usePolicy: String?
    let notes: String?

    var id: String { capabilityID }

    enum CodingKeys: String, CodingKey {
        case capabilityID = "capability_id"
        case label
        case category
        case evidenceKind = "evidence_kind"
        case source
        case confidence
        case reviewStatus = "review_status"
        case usePolicy = "use_policy"
        case notes
    }
}

struct NPCCapabilityFactsRecord: Decodable, Identifiable {
    let npcID: String
    let capabilities: [NPCCapabilityFact]
    let sourceNote: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case capabilities
        case sourceNote = "source_note"
        case reviewStatus = "review_status"
    }
}

struct NPCEducationFact: Decodable {
    let kind: String
    let label: String
    let confidence: String?
    let source: String?
}

struct NPCEducationOriginRecord: Decodable, Identifiable {
    let npcID: String
    let examDegree: String
    let examYear: Int?
    let cohortID: String?
    let familyOrigin: String
    let trainingPaths: [String]
    let facts: [NPCEducationFact]
    let sourceNote: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case examDegree = "exam_degree"
        case examYear = "exam_year"
        case cohortID = "cohort_id"
        case familyOrigin = "family_origin"
        case trainingPaths = "training_paths"
        case facts
        case sourceNote = "source_note"
        case reviewStatus = "review_status"
    }
}

struct NPCCulturalLiteracyRecord: Decodable, Identifiable {
    let npcID: String
    let profileVersion: Int
    let displayName: String
    let level: Int
    let label: String
    let rationale: String?
    let sourceInputs: [String]?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?
    let usePolicy: String?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case profileVersion = "profile_version"
        case displayName = "display_name"
        case level
        case label
        case rationale
        case sourceInputs = "source_inputs"
        case confidence
        case reviewStatus = "review_status"
        case usePolicy = "use_policy"
    }
}

struct NPCLegacyCostumeHint: Decodable {
    let rankGrade: Int
    let rankLabel: String
    let rankCategory: String
    let usePolicy: String?

    enum CodingKeys: String, CodingKey {
        case rankGrade = "rank_grade"
        case rankLabel = "rank_label"
        case rankCategory = "rank_category"
        case usePolicy = "use_policy"
    }
}

struct NPCRankTitleRecord: Decodable, Identifiable {
    let npcID: String
    let primaryOfficeCatalogID: String?
    let openingTitleState: String
    let openingPresenceState: String
    let openingRuntimeScope: String
    let titleUsagePolicy: String
    let rankContext: RankContext
    let titleSystem: String
    let titleName: String
    let officialRankCode: OfficialRankCode?
    let rankApplicability: RankApplicability
    let titleOrder: Int?
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?
    let sourceNote: String?
    let legacyCostumeHint: NPCLegacyCostumeHint?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case primaryOfficeCatalogID = "primary_office_catalog_id"
        case openingTitleState = "opening_title_state"
        case openingPresenceState = "opening_presence_state"
        case openingRuntimeScope = "opening_runtime_scope"
        case titleUsagePolicy = "title_usage_policy"
        case rankContext = "rank_context"
        case titleSystem = "title_system"
        case titleName = "title_name"
        case officialRankCode = "official_rank_code"
        case rankApplicability = "rank_applicability"
        case titleOrder = "title_order"
        case source
        case confidence
        case reviewStatus = "review_status"
        case sourceNote = "source_note"
        case legacyCostumeHint = "legacy_costume_hint"
    }
}

struct NPCAppointmentRecord: Decodable, Identifiable {
    let appointmentID: String
    let npcID: String
    let officeCatalogID: String
    let institutionID: String
    let officeSlotID: String
    let environmentOfficePostID: String?
    let environmentOfficeCanonicalTitle: String?
    let environmentOfficeMatchStatus: String?
    let officeCapacityPolicy: String
    let officeTitle: String
    let appointmentKind: AppointmentKind
    let rankContext: RankContext
    let officialRankCode: OfficialRankCode?
    let rankApplicability: RankApplicability
    let active: Bool
    let occupiesOfficeCapacity: Bool
    let startYear: Int?
    let endYear: Int?
    let sourceOfficeText: String?
    let source: String?
    let reviewStatus: NPCReviewStatus?
    let notes: [String]?

    var id: String { appointmentID }

    enum CodingKeys: String, CodingKey {
        case appointmentID = "appointment_id"
        case npcID = "npc_id"
        case officeCatalogID = "office_catalog_id"
        case institutionID = "institution_id"
        case officeSlotID = "office_slot_id"
        case environmentOfficePostID = "environment_office_post_id"
        case environmentOfficeCanonicalTitle = "environment_office_canonical_title"
        case environmentOfficeMatchStatus = "environment_office_match_status"
        case officeCapacityPolicy = "office_capacity_policy"
        case officeTitle = "office_title"
        case appointmentKind = "appointment_kind"
        case rankContext = "rank_context"
        case officialRankCode = "official_rank_code"
        case rankApplicability = "rank_applicability"
        case active
        case occupiesOfficeCapacity = "occupies_office_capacity"
        case startYear = "start_year"
        case endYear = "end_year"
        case sourceOfficeText = "source_office_text"
        case source
        case reviewStatus = "review_status"
        case notes
    }
}

struct NPCStatusTimelineItem: Decodable {
    let year: Int
    let month: Int?
    let status: NPCStatusCode
    let presenceState: String
    let runtimeScope: String
    let reason: String
    let source: String?

    enum CodingKeys: String, CodingKey {
        case year
        case month
        case status
        case presenceState = "presence_state"
        case runtimeScope = "runtime_scope"
        case reason
        case source
    }
}

struct NPCStatusRecord: Decodable, Identifiable {
    let npcID: String
    let currentStatus: NPCStatusCode
    let currentPresenceState: String
    let currentRuntimeScope: String
    let summonable: Bool
    let appointable: Bool
    let location: NPCLocationFact
    let legacyStatus: String?
    let timeline: [NPCStatusTimelineItem]

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case currentStatus = "current_status"
        case currentPresenceState = "current_presence_state"
        case currentRuntimeScope = "current_runtime_scope"
        case summonable
        case appointable
        case location
        case legacyStatus = "legacy_status"
        case timeline
    }
}

struct NPCDebutWindow: Decodable {
    let year: Int?
    let month: Int?
    let source: String?
    let confidence: String?
}

struct NPCAvailableFromWindow: Decodable {
    let year: Int
    let month: Int?
    let policy: String
}

struct NPCHistoricalExitWindow: Decodable {
    let year: Int?
    let month: Int?
    let kind: String
    let source: String?
    let confidence: String?
}

struct NPCLifecycleWindowRecord: Decodable, Identifiable {
    let npcID: String
    let gameStartYear: Int
    let startStatus: NPCStatusCode
    let availability: String
    let debut: NPCDebutWindow
    let availableFrom: NPCAvailableFromWindow
    let historicalExit: NPCHistoricalExitWindow
    let startLocation: NPCLocationFact
    let sourceInputs: [String]?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case gameStartYear = "game_start_year"
        case startStatus = "start_status"
        case availability
        case debut
        case availableFrom = "available_from"
        case historicalExit = "historical_exit"
        case startLocation = "start_location"
        case sourceInputs = "source_inputs"
        case reviewStatus = "review_status"
    }
}

struct NPCAffiliationItem: Decodable {
    let kind: String
    let id: String
    let label: String
    let source: String?
    let confidence: String?
}

struct NPCAffiliationRecord: Decodable, Identifiable {
    let npcID: String
    let affiliations: [NPCAffiliationItem]
    let tags: [String]
    let legacyOfficeType: String?
    let sourceNote: String?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case affiliations
        case tags
        case legacyOfficeType = "legacy_office_type"
        case sourceNote = "source_note"
    }
}

struct NPCRelationshipEdge: Decodable, Identifiable {
    let edgeID: String
    let fromNPCID: String
    let toNPCID: String
    let relationshipKind: RelationshipKind
    let rawType: String
    let polarity: Int
    let intensity: Int
    let trust: Int
    let obligation: Int
    let publicity: String
    let confidence: String?
    let evidenceStatus: String?
    let evidenceClass: String?
    let derivationPolicy: String?
    let note: String
    let source: String?

    var id: String { edgeID }

    enum CodingKeys: String, CodingKey {
        case edgeID = "edge_id"
        case fromNPCID = "from_npc_id"
        case toNPCID = "to_npc_id"
        case relationshipKind = "relationship_kind"
        case rawType = "raw_type"
        case polarity
        case intensity
        case trust
        case obligation
        case publicity
        case confidence
        case evidenceStatus = "evidence_status"
        case evidenceClass = "evidence_class"
        case derivationPolicy = "derivation_policy"
        case note
        case source
    }
}

struct NPCSocialCapitalEdgeCounts: Decodable {
    let total: Int
    let verified: Int?
    let derived: Int?
    let unverified: Int?
}

struct NPCSocialCapitalMetric: Decodable, Identifiable {
    let metricID: String
    let metricName: String
    let value: Double
    let rationale: String?
    let sourceInputs: [String]?

    var id: String { metricID }

    enum CodingKeys: String, CodingKey {
        case metricID = "metric_id"
        case metricName = "metric_name"
        case value
        case rationale
        case sourceInputs = "source_inputs"
    }
}

struct NPCSocialCapitalTopRelationship: Decodable, Identifiable {
    let edgeID: String
    let otherNPCID: String
    let relationshipKind: RelationshipKind
    let rawType: String
    let evidenceStatus: String?
    let evidenceClass: String?
    let weightedScore: Double
    let reason: String

    var id: String { edgeID }

    enum CodingKeys: String, CodingKey {
        case edgeID = "edge_id"
        case otherNPCID = "other_npc_id"
        case relationshipKind = "relationship_kind"
        case rawType = "raw_type"
        case evidenceStatus = "evidence_status"
        case evidenceClass = "evidence_class"
        case weightedScore = "weighted_score"
        case reason
    }
}

struct NPCSocialCapitalRecord: Decodable, Identifiable {
    let npcID: String
    let modelVersion: Int
    let displayName: String
    let networkRole: String
    let edgeCounts: NPCSocialCapitalEdgeCounts
    let metrics: [NPCSocialCapitalMetric]
    let topRelationships: [NPCSocialCapitalTopRelationship]
    let source: String?
    let sourceInputs: [String]?
    let usePolicy: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case modelVersion = "model_version"
        case displayName = "display_name"
        case networkRole = "network_role"
        case edgeCounts = "edge_counts"
        case metrics
        case topRelationships = "top_relationships"
        case source
        case sourceInputs = "source_inputs"
        case usePolicy = "use_policy"
        case reviewStatus = "review_status"
    }
}

struct NPCTiangangValue: Decodable, Identifiable {
    let dimensionID: String
    let dimensionName: String
    let dimensionType: String
    let value: Int
    let label: String
    let rationale: String?
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { dimensionID }

    enum CodingKeys: String, CodingKey {
        case dimensionID = "dimension_id"
        case dimensionName = "dimension_name"
        case dimensionType = "dimension_type"
        case value
        case label
        case rationale
        case source
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCTiangangProfileRecord: Decodable, Identifiable {
    let npcID: String
    let profileVersion: Int
    let values: [NPCTiangangValue]
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case profileVersion = "profile_version"
        case values
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCXinpanConcern: Decodable, Identifiable {
    let dimensionID: String
    let npcValue: Int
    let weight: Double
    let reason: String

    var id: String { dimensionID }

    enum CodingKeys: String, CodingKey {
        case dimensionID = "dimension_id"
        case npcValue = "npc_value"
        case weight
        case reason
    }
}

struct NPCXinpanInitialState: Decodable {
    let daoHe: Double
    let shiHe: Double
    let fear: Double
    let trustCoeff: Double
    let hatred: Double
    let quadrant: String

    enum CodingKeys: String, CodingKey {
        case daoHe = "dao_he"
        case shiHe = "shi_he"
        case fear
        case trustCoeff = "trust_coeff"
        case hatred
        case quadrant
    }
}

struct NPCXinpanDerivation: Decodable {
    let method: String
    let sourceInputs: [String]?
    let initializationRules: [String]?
    let runtimeLogsPolicy: String

    enum CodingKeys: String, CodingKey {
        case method
        case sourceInputs = "source_inputs"
        case initializationRules = "initialization_rules"
        case runtimeLogsPolicy = "runtime_logs_policy"
    }
}

struct NPCXinpanSeedRecord: Decodable, Identifiable {
    let npcID: String
    let modelVersion: Int
    let coreConcerns: [NPCXinpanConcern]
    let initialState: NPCXinpanInitialState
    let derivation: NPCXinpanDerivation?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case modelVersion = "model_version"
        case coreConcerns = "core_concerns"
        case initialState = "initial_state"
        case derivation
        case reviewStatus = "review_status"
    }
}

struct NPCMingshuAxis: Decodable, Identifiable {
    let axisID: String
    let axisName: String
    let value: Int
    let label: String
    let rationale: String?
    let sourceInputs: [String]?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { axisID }

    enum CodingKeys: String, CodingKey {
        case axisID = "axis_id"
        case axisName = "axis_name"
        case value
        case label
        case rationale
        case sourceInputs = "source_inputs"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCMingshuProfileRecord: Decodable, Identifiable {
    let npcID: String
    let profileVersion: Int
    let displayName: String
    let mingshuArchetype: String
    let profileRationale: String?
    let axes: [NPCMingshuAxis]
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case profileVersion = "profile_version"
        case displayName = "display_name"
        case mingshuArchetype = "mingshu_archetype"
        case profileRationale = "profile_rationale"
        case axes
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCBiographyArcRecord: Decodable, Identifiable {
    let npcID: String
    let publicBiography: String
    let careerOrigin: String
    let riseHooks: [String]
    let riskHooks: [String]
    let politicalUseHooks: [String]
    let legacyAbilityLogicArchive: String?
    let archivePolicy: String?
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case publicBiography = "public_biography"
        case careerOrigin = "career_origin"
        case riseHooks = "rise_hooks"
        case riskHooks = "risk_hooks"
        case politicalUseHooks = "political_use_hooks"
        case legacyAbilityLogicArchive = "legacy_ability_logic_archive"
        case archivePolicy = "archive_policy"
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCHistoricalBiographySource: Decodable, Identifiable {
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

struct NPCHistoricalBiographyRecord: Decodable, Identifiable {
    let npcID: String
    let biographyStyle: String
    let biographyText: String
    let factSources: [NPCHistoricalBiographySource]?
    let anecdoteSources: [NPCHistoricalBiographySource]?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case biographyStyle = "biography_style"
        case biographyText = "biography_text"
        case factSources = "fact_sources"
        case anecdoteSources = "anecdote_sources"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCMingpiProsodyCheck: Decodable {
    let passed: Bool
    let notes: String
}

struct NPCMingpiRecord: Decodable, Identifiable {
    let npcID: String
    let profileVersion: Int
    let displayName: String
    let formID: String
    let formLabel: String
    let cipai: String?
    let qupai: String?
    let title: String
    let lines: [String]
    let prosodyCheck: NPCMingpiProsodyCheck

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case profileVersion = "profile_version"
        case displayName = "display_name"
        case formID = "form_id"
        case formLabel = "form_label"
        case cipai
        case qupai
        case title
        case lines
        case prosodyCheck = "prosody_check"
    }
}

struct NPCStart1628PositionRecord: Decodable, Identifiable {
    let npcID: String
    let startYear: Int
    let ageAtStart: Int?
    let ageBasis: String
    let gameEstimatedAgeAtStart: Int?
    let ageEstimateBasis: String
    let startStatus: NPCStatusCode
    let startOfficeTitle: String
    let officeHoldingState: String
    let startPresenceState: String
    let startRuntimeScope: String
    let institutionID: String
    let environmentOfficePostID: String?
    let environmentOfficeCanonicalTitle: String?
    let environmentOfficeMatchStatus: String?
    let officeCapacityPolicy: String
    let rankContext: RankContext
    let officialRankCode: OfficialRankCode?
    let rankApplicability: RankApplicability
    let placementClass: String
    let sourceBasis: String?
    let fictionalAdjustmentNote: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case startYear = "start_year"
        case ageAtStart = "age_at_start"
        case ageBasis = "age_basis"
        case gameEstimatedAgeAtStart = "game_estimated_age_at_start"
        case ageEstimateBasis = "age_estimate_basis"
        case startStatus = "start_status"
        case startOfficeTitle = "start_office_title"
        case officeHoldingState = "office_holding_state"
        case startPresenceState = "start_presence_state"
        case startRuntimeScope = "start_runtime_scope"
        case institutionID = "institution_id"
        case environmentOfficePostID = "environment_office_post_id"
        case environmentOfficeCanonicalTitle = "environment_office_canonical_title"
        case environmentOfficeMatchStatus = "environment_office_match_status"
        case officeCapacityPolicy = "office_capacity_policy"
        case rankContext = "rank_context"
        case officialRankCode = "official_rank_code"
        case rankApplicability = "rank_applicability"
        case placementClass = "placement_class"
        case sourceBasis = "source_basis"
        case fictionalAdjustmentNote = "fictional_adjustment_note"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCAppearanceAxisCatalogRecord: Decodable, Identifiable {
    let axisID: String
    let axisName: String
    let group: String
    let scaleMin: Int
    let scaleMax: Int
    let anchor1: String
    let anchor4: String
    let anchor7: String
    let promptHint: String
    let source: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { axisID }

    enum CodingKeys: String, CodingKey {
        case axisID = "axis_id"
        case axisName = "axis_name"
        case group
        case scaleMin = "scale_min"
        case scaleMax = "scale_max"
        case anchor1 = "anchor_1"
        case anchor4 = "anchor_4"
        case anchor7 = "anchor_7"
        case promptHint = "prompt_hint"
        case source
        case reviewStatus = "review_status"
    }
}

struct NPCAppearanceAxisValue: Decodable, Identifiable {
    let axisID: String
    let axisName: String
    let group: String
    let value: Int
    let label: String
    let rationale: String?
    let sourceInputs: [String]?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { axisID }

    enum CodingKeys: String, CodingKey {
        case axisID = "axis_id"
        case axisName = "axis_name"
        case group
        case value
        case label
        case rationale
        case sourceInputs = "source_inputs"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCDistinctiveMark: Decodable, Identifiable {
    let markType: String
    let region: String
    let size: Int
    let visibility: Int
    let promptPhrase: String
    let source: String?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { "\(markType)-\(region)-\(promptPhrase)" }

    enum CodingKeys: String, CodingKey {
        case markType = "mark_type"
        case region
        case size = "size_1_7"
        case visibility = "visibility_1_7"
        case promptPhrase = "prompt_phrase"
        case source
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCExcludedLegacyFragment: Decodable, Identifiable {
    let fragment: String
    let reason: String
    let source: String?
    let archivePolicy: String?

    var id: String { "\(source ?? "")-\(reason)-\(fragment)" }

    enum CodingKeys: String, CodingKey {
        case fragment
        case reason
        case source
        case archivePolicy = "archive_policy"
    }
}

struct NPCAppearanceProfileRecord: Decodable, Identifiable {
    let npcID: String
    let profileVersion: Int
    let displayName: String
    let dnaSeed: String
    let visualContextTags: [String]
    let axes: [NPCAppearanceAxisValue]
    let distinctiveMarks: [NPCDistinctiveMark]
    let excludedLegacyFragments: [NPCExcludedLegacyFragment]?
    let sourceInputs: [String]?
    let confidence: String?
    let reviewStatus: NPCReviewStatus?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case profileVersion = "profile_version"
        case displayName = "display_name"
        case dnaSeed = "dna_seed"
        case visualContextTags = "visual_context_tags"
        case axes
        case distinctiveMarks = "distinctive_marks"
        case excludedLegacyFragments = "excluded_legacy_fragments"
        case sourceInputs = "source_inputs"
        case confidence
        case reviewStatus = "review_status"
    }
}

struct NPCAssetRecord: Decodable, Identifiable {
    let npcID: String
    let portraitAsset: String
    let assetStatus: String?
    let dnaAssetID: String
    let dnaSeed: String
    let wardrobeKey: String
    let source: String?
    let legacyCostumeHint: NPCLegacyCostumeHint?
    let promptArchivePolicy: String?

    var id: String { npcID }

    enum CodingKeys: String, CodingKey {
        case npcID = "npc_id"
        case portraitAsset = "portrait_asset"
        case assetStatus = "asset_status"
        case dnaAssetID = "dna_asset_id"
        case dnaSeed = "dna_seed"
        case wardrobeKey = "wardrobe_key"
        case source
        case legacyCostumeHint = "legacy_costume_hint"
        case promptArchivePolicy = "prompt_archive_policy"
    }
}

struct NPCDatabase {
    let manifest: NPCDatabaseManifest
    let rankSystemCatalog: [NPCRankSystemCatalogRecord]
    let statusCatalog: [NPCStatusCatalogRecord]
    let institutionCatalog: [NPCInstitutionCatalogRecord]
    let officeCatalog: [NPCOfficeCatalogRecord]
    let appearanceAxisCatalog: [NPCAppearanceAxisCatalogRecord]
    let culturalLiteracyCatalog: [NPCCulturalLiteracyCatalogRecord]
    let skillAnchorCatalog: [NPCSkillAnchorCatalogRecord]
    let core: [NPCCoreRecord]
    let educationOrigins: [NPCEducationOriginRecord]
    let culturalLiteracy: [NPCCulturalLiteracyRecord]
    let rankTitles: [NPCRankTitleRecord]
    let appointments: [NPCAppointmentRecord]
    let statuses: [NPCStatusRecord]
    let affiliations: [NPCAffiliationRecord]
    let relationships: [NPCRelationshipEdge]
    let socialCapital: [NPCSocialCapitalRecord]
    let tiangangProfiles: [NPCTiangangProfileRecord]
    let xinpanSeeds: [NPCXinpanSeedRecord]
    let mingshuProfiles: [NPCMingshuProfileRecord]
    let appearanceProfiles: [NPCAppearanceProfileRecord]
    let capabilityFacts: [NPCCapabilityFactsRecord]
    let lifecycleWindows: [NPCLifecycleWindowRecord]
    let biographyArcs: [NPCBiographyArcRecord]
    let historicalBiographies: [NPCHistoricalBiographyRecord]
    let mingpiRecords: [NPCMingpiRecord]
    let start1628Positions: [NPCStart1628PositionRecord]
    let assets: [NPCAssetRecord]

    var coreByID: [String: NPCCoreRecord] {
        Dictionary(uniqueKeysWithValues: core.map { ($0.npcID, $0) })
    }

    func coreRecord(named name: String) -> NPCCoreRecord? {
        core.first { $0.canonicalName == name || $0.aliases.contains(name) }
    }
}

enum NPCDatabaseLoadError: Error, LocalizedError {
    case missingResource(String)

    var errorDescription: String? {
        switch self {
        case .missingResource(let name):
            return "Missing NPC database resource: \(name).json"
        }
    }
}

final class NPCDatabaseStore {
    let database: NPCDatabase

    init(bundle: Bundle = .main) throws {
        let manifest = try Self.decodeManifest(bundle: bundle)
        database = NPCDatabase(
            manifest: manifest,
            rankSystemCatalog: try Self.decodeSeed("rank_system_catalog_seed", bundle: bundle),
            statusCatalog: try Self.decodeSeed("status_catalog_seed", bundle: bundle),
            institutionCatalog: try Self.decodeSeed("institution_catalog_seed", bundle: bundle),
            officeCatalog: try Self.decodeSeed("office_catalog_seed", bundle: bundle),
            appearanceAxisCatalog: try Self.decodeSeed("appearance_axis_catalog_seed", bundle: bundle),
            culturalLiteracyCatalog: try Self.decodeSeed("cultural_literacy_catalog_seed", bundle: bundle),
            skillAnchorCatalog: try Self.decodeSeed("skill_anchor_catalog_seed", bundle: bundle),
            core: try Self.decodeSeed("npc_core_seed", bundle: bundle),
            educationOrigins: try Self.decodeSeed("npc_education_origin_seed", bundle: bundle),
            culturalLiteracy: try Self.decodeSeed("npc_cultural_literacy_seed", bundle: bundle),
            rankTitles: try Self.decodeSeed("npc_rank_titles_seed", bundle: bundle),
            appointments: try Self.decodeSeed("npc_appointments_seed", bundle: bundle),
            statuses: try Self.decodeSeed("npc_status_timeline_seed", bundle: bundle),
            affiliations: try Self.decodeSeed("npc_affiliations_seed", bundle: bundle),
            relationships: try Self.decodeSeed("npc_relationship_edges_seed", bundle: bundle),
            socialCapital: try Self.decodeSeed("npc_social_capital_seed", bundle: bundle),
            tiangangProfiles: try Self.decodeSeed("npc_tiangang_profiles_seed", bundle: bundle),
            xinpanSeeds: try Self.decodeSeed("npc_xinpan_seed", bundle: bundle),
            mingshuProfiles: try Self.decodeSeed("npc_mingshu_profiles_seed", bundle: bundle),
            appearanceProfiles: try Self.decodeSeed("npc_appearance_profiles_seed", bundle: bundle),
            capabilityFacts: try Self.decodeSeed("npc_capability_facts_seed", bundle: bundle),
            lifecycleWindows: try Self.decodeSeed("npc_lifecycle_windows_seed", bundle: bundle),
            biographyArcs: try Self.decodeSeed("npc_biography_arcs_seed", bundle: bundle),
            historicalBiographies: try Self.decodeSeed("npc_historical_biographies_seed", bundle: bundle),
            mingpiRecords: try Self.decodeSeed("npc_mingpi_seed", bundle: bundle),
            start1628Positions: try Self.decodeSeed("npc_start_1628_positions_seed", bundle: bundle),
            assets: try Self.decodeSeed("npc_assets_seed", bundle: bundle)
        )
    }

    private static func decodeSeed<Record: Decodable>(_ resource: String, bundle: Bundle) throws -> [Record] {
        let url = try resourceURL(resource, bundle: bundle)
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(NPCSeedFile<Record>.self, from: data).records
    }

    private static func decodeManifest(bundle: Bundle) throws -> NPCDatabaseManifest {
        let url = try resourceURL("npc_database_manifest", bundle: bundle)
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(NPCDatabaseManifest.self, from: data)
    }

    private static func resourceURL(_ resource: String, bundle: Bundle) throws -> URL {
        if let url = bundle.url(forResource: resource, withExtension: "json", subdirectory: "NPCDatabase") {
            return url
        }
        if let url = bundle.url(forResource: resource, withExtension: "json") {
            return url
        }
        throw NPCDatabaseLoadError.missingResource(resource)
    }
}
