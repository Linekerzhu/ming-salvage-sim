import Foundation
import SwiftUI
import UIKit

struct TiangangRootView: View {
    @StateObject private var store = StarCatalogStore()
    @State private var selectedTab: StarCatalogTab = .overview
    @State private var isShowingFilters = false

    var body: some View {
        NavigationStack {
            ZStack {
                StarPalette.background.ignoresSafeArea()

                if let error = store.loadError {
                    ContentUnavailableView("群星谱未载入", systemImage: "exclamationmark.triangle", description: Text(error))
                } else if store.profiles.isEmpty {
                    ContentUnavailableView("群星未入册", systemImage: "book.closed", description: Text("NPC 数据基座为空。"))
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            StarHeaderView(
                                totalCount: store.profiles.count,
                                visibleCount: store.filteredProfiles.count,
                                activeFilterCount: store.activeFilterCount,
                                onFilterTapped: { isShowingFilters = true }
                            )
                                .padding(.horizontal, 16)
                                .padding(.top, 12)
                                .padding(.bottom, 6)

                            if store.filteredProfiles.isEmpty {
                                FilteredStarEmptyState(resetFilters: store.resetFilters)
                                    .padding(.horizontal, 16)
                                    .padding(.top, 22)
                            } else {
                                StarPortraitPager(store: store)
                                    .padding(.bottom, 8)

                                StarTabRail(selectedTab: $selectedTab)
                                    .padding(.horizontal, 16)
                                    .padding(.bottom, 10)

                                if let profile = store.selectedProfile {
                                    StarTabPage(
                                        profile: profile,
                                        selectedTab: selectedTab,
                                        nameLookup: store.name(for:),
                                        tiangangDimensionsByID: store.tiangangDimensionsByID,
                                        tiangangGroups: store.tiangangGroups,
                                        skillAnchorsByDimensionID: store.skillAnchorsByDimensionID
                                    )
                                    .padding(.horizontal, 16)
                                }
                            }
                        }
                        .padding(.bottom, 34)
                    }
                }
            }
            .navigationTitle("群星谱")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .navigationBar)
            .sheet(isPresented: $isShowingFilters) {
                StarFilterSheet(
                    filter: $store.filter,
                    options: store.filterOptions,
                    resetFilters: store.resetFilters
                )
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
    }
}

@MainActor
private final class StarCatalogStore: ObservableObject {
    @Published private(set) var profiles: [StarProfile] = []
    @Published private(set) var loadError: String?
    @Published private(set) var tiangangDimensionsByID: [String: TiangangDimension] = [:]
    @Published private(set) var tiangangGroups: [String] = []
    @Published private(set) var skillAnchorsByDimensionID: [String: NPCSkillAnchorCatalogRecord] = [:]
    @Published var filter = StarCatalogFilterState() {
        didSet { normalizeSelectionForCurrentFilter() }
    }
    @Published var selectedNPCID: String = ""

    private var namesByID: [String: String] = [:]

    init() {
        load()
    }

    var selectedProfile: StarProfile? {
        filteredProfiles.first { $0.id == selectedNPCID } ?? filteredProfiles.first
    }

    var selectedIndex: Int {
        filteredProfiles.firstIndex { $0.id == selectedNPCID } ?? 0
    }

    var filteredProfiles: [StarProfile] {
        profiles.filter { filter.allows($0) }
    }

    var activeFilterCount: Int {
        filter.activeCount
    }

    var filterOptions: StarCatalogFilterOptions {
        StarCatalogFilterOptions(profiles: profiles)
    }

    func name(for npcID: String) -> String {
        namesByID[npcID] ?? npcID.replacingOccurrences(of: "npc_ming_", with: "")
    }

    func resetFilters() {
        filter = StarCatalogFilterState()
    }

    private func load() {
        do {
            let database = try NPCDatabaseStore().database
            let environment = try EnvironmentDatabaseStore().database
            let tiangangDimensions = try Self.loadTiangangDimensions()
            namesByID = Dictionary(uniqueKeysWithValues: database.core.map { ($0.npcID, $0.canonicalName) })
            tiangangDimensionsByID = Dictionary(uniqueKeysWithValues: tiangangDimensions.map { ($0.id, $0) })
            tiangangGroups = tiangangDimensions.reduce(into: [String]()) { groups, dimension in
                if !groups.contains(dimension.group) {
                    groups.append(dimension.group)
                }
            }
            skillAnchorsByDimensionID = database.skillAnchorCatalog.reduce(into: [String: NPCSkillAnchorCatalogRecord]()) { result, record in
                guard let dimensionID = record.linkedDimensionID else { return }
                result[dimensionID] = record
            }

            let rankByID = Dictionary(uniqueKeysWithValues: database.rankTitles.map { ($0.npcID, $0) })
            let startByID = Dictionary(uniqueKeysWithValues: database.start1628Positions.map { ($0.npcID, $0) })
            let biographyByID = Dictionary(uniqueKeysWithValues: database.historicalBiographies.map { ($0.npcID, $0) })
            let arcByID = Dictionary(uniqueKeysWithValues: database.biographyArcs.map { ($0.npcID, $0) })
            let tiangangByID = Dictionary(uniqueKeysWithValues: database.tiangangProfiles.map { ($0.npcID, $0) })
            let mingshuByID = Dictionary(uniqueKeysWithValues: database.mingshuProfiles.map { ($0.npcID, $0) })
            let xinpanByID = Dictionary(uniqueKeysWithValues: database.xinpanSeeds.map { ($0.npcID, $0) })
            let socialByID = Dictionary(uniqueKeysWithValues: database.socialCapital.map { ($0.npcID, $0) })
            let assetByID = Dictionary(uniqueKeysWithValues: database.assets.map { ($0.npcID, $0) })
            let educationByID = Dictionary(uniqueKeysWithValues: database.educationOrigins.map { ($0.npcID, $0) })
            let literacyByID = Dictionary(uniqueKeysWithValues: database.culturalLiteracy.map { ($0.npcID, $0) })
            let literacyCatalogByLevel = Dictionary(uniqueKeysWithValues: database.culturalLiteracyCatalog.map { ($0.level, $0) })
            let capabilityByID = Dictionary(uniqueKeysWithValues: database.capabilityFacts.map { ($0.npcID, $0) })
            let npcInstitutionByID = Dictionary(uniqueKeysWithValues: database.institutionCatalog.map { ($0.institutionID, $0) })
            let environmentInstitutionByID = Dictionary(uniqueKeysWithValues: environment.institutions.map { ($0.institutionID, $0) })
            let environmentOfficePostByID = environment.officePostsByID

            var relationshipsByID: [String: [NPCRelationshipEdge]] = [:]
            for edge in database.relationships {
                relationshipsByID[edge.fromNPCID, default: []].append(edge)
                relationshipsByID[edge.toNPCID, default: []].append(edge)
            }

            profiles = database.core.map { core in
                let start = startByID[core.npcID]
                let literacy = literacyByID[core.npcID]
                let institutionID = start?.institutionID
                let environmentOfficePostID = start?.environmentOfficePostID
                return StarProfile(
                    core: core,
                    rank: rankByID[core.npcID],
                    start: start,
                    biography: biographyByID[core.npcID],
                    arc: arcByID[core.npcID],
                    tiangang: tiangangByID[core.npcID],
                    mingshu: mingshuByID[core.npcID],
                    xinpan: xinpanByID[core.npcID],
                    social: socialByID[core.npcID],
                    asset: assetByID[core.npcID],
                    education: educationByID[core.npcID],
                    literacy: literacy,
                    literacyCatalog: literacy.flatMap { literacyCatalogByLevel[$0.level] },
                    capabilities: capabilityByID[core.npcID],
                    relationships: relationshipsByID[core.npcID] ?? [],
                    officeContext: StarOfficeContext(
                        start: start,
                        rank: rankByID[core.npcID],
                        npcInstitution: institutionID.flatMap { npcInstitutionByID[$0] },
                        environmentInstitution: institutionID.flatMap { environmentInstitutionByID[$0] },
                        environmentOfficePost: environmentOfficePostID.flatMap { environmentOfficePostByID[$0] }
                    )
                )
            }

            selectedNPCID = profiles.first?.id ?? ""
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func normalizeSelectionForCurrentFilter() {
        let visible = filteredProfiles
        guard !visible.isEmpty else { return }
        if !visible.contains(where: { $0.id == selectedNPCID }) {
            selectedNPCID = visible[0].id
        }
    }

    private static func loadTiangangDimensions() throws -> [TiangangDimension] {
        guard let url = Bundle.main.url(forResource: "tiangang_seed", withExtension: "json") else {
            throw NPCDatabaseLoadError.missingResource("tiangang_seed")
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(TiangangCatalog.self, from: data).meta.dimensions
    }
}

private struct StarCatalogFilterState: Equatable {
    var identities: Set<StarIdentityKind> = []
    var powerIDs: Set<String> = []
    var statuses: Set<NPCStatusCode> = []
    var rankBands: Set<StarRankBand> = []
    var literacyLevels: Set<Int> = []
    var portrait: StarPortraitFilter = .all

    var activeCount: Int {
        identities.count
        + powerIDs.count
        + statuses.count
        + rankBands.count
        + literacyLevels.count
        + (portrait == .all ? 0 : 1)
    }

    func allows(_ profile: StarProfile) -> Bool {
        if !identities.isEmpty, !identities.contains(profile.identityKind) {
            return false
        }
        if !powerIDs.isEmpty, !powerIDs.contains(profile.core.powerID) {
            return false
        }
        if !statuses.isEmpty, !statuses.contains(profile.start?.startStatus ?? .activeUnassigned) {
            return false
        }
        if !rankBands.isEmpty, !rankBands.contains(where: { $0.contains(profile) }) {
            return false
        }
        if !literacyLevels.isEmpty, !literacyLevels.contains(profile.literacy?.level ?? 0) {
            return false
        }
        switch portrait {
        case .all:
            return true
        case .available:
            return profile.hasPortrait
        case .missing:
            return !profile.hasPortrait
        }
    }
}

private struct StarCatalogFilterOptions {
    let powers: [StarPowerOption]
    let statuses: [NPCStatusCode]
    let literacyOptions: [StarLiteracyOption]

    init(profiles: [StarProfile]) {
        let powerIDs = Set(profiles.map(\.core.powerID))
        powers = powerIDs
            .map { StarPowerOption(id: $0, label: StarPowerOption.label(for: $0)) }
            .sorted { $0.label < $1.label }

        let statusOrder = NPCStatusCode.filterOrder
        let seenStatuses = Set(profiles.map { $0.start?.startStatus ?? .activeUnassigned })
        statuses = statusOrder.filter { seenStatuses.contains($0) }

        let literacyByLevel = profiles.reduce(into: [Int: String]()) { result, profile in
            guard let literacy = profile.literacy else { return }
            result[literacy.level] = literacy.label
        }
        literacyOptions = literacyByLevel
            .map { StarLiteracyOption(level: $0.key, label: $0.value) }
            .sorted { $0.level > $1.level }
    }
}

private struct StarLiteracyOption: Identifiable, Hashable {
    let level: Int
    let label: String

    var id: Int { level }
}

private struct StarPowerOption: Identifiable, Hashable {
    let id: String
    let label: String

    static func label(for powerID: String) -> String {
        switch powerID {
        case "ming": return "大明"
        case "houjin": return "后金"
        case "bandits": return "流寇"
        case "mongol": return "蒙古"
        case "korea": return "朝鲜"
        default: return powerID.isEmpty ? "未详" : powerID
        }
    }
}

private enum StarRankBand: String, CaseIterable, Identifiable {
    case high
    case middle
    case low
    case unranked

    var id: String { rawValue }

    var title: String {
        switch self {
        case .high: return "一至三品"
        case .middle: return "四至六品"
        case .low: return "七至九品"
        case .unranked: return "位序/无品"
        }
    }

    func contains(_ profile: StarProfile) -> Bool {
        guard let grade = (profile.start?.officialRankCode ?? profile.rank?.officialRankCode)?.grade else {
            return self == .unranked
        }
        switch self {
        case .high:
            return (1...3).contains(grade)
        case .middle:
            return (4...6).contains(grade)
        case .low:
            return (7...9).contains(grade)
        case .unranked:
            return false
        }
    }
}

private enum StarPortraitFilter: String, CaseIterable, Identifiable {
    case all
    case available
    case missing

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: return "全部"
        case .available: return "有立绘"
        case .missing: return "缺立绘"
        }
    }
}

private struct StarOfficeContext {
    let institutionName: String
    let officeTitle: String
    let capacityPolicy: String

    init(
        start: NPCStart1628PositionRecord?,
        rank: NPCRankTitleRecord?,
        npcInstitution: NPCInstitutionCatalogRecord?,
        environmentInstitution: MingInstitutionRecord?,
        environmentOfficePost: MingOfficePostRecord?
    ) {
        institutionName = firstKnown(environmentInstitution?.name, npcInstitution?.label, start?.institutionID) ?? "未详"
        officeTitle = firstKnown(
            environmentOfficePost?.canonicalTitle,
            start?.environmentOfficeCanonicalTitle,
            start?.startOfficeTitle,
            rank?.titleName
        ) ?? "未详"
        capacityPolicy = start?.officeCapacityPolicy ?? environmentOfficePost?.capacityPolicy ?? ""
    }
}

private struct StarProfileTag: Identifiable {
    let id: String
    let text: String
    let color: Color
}

private enum StarHookKind {
    case positive
    case risk
}

private struct StarHookTag: Identifiable, Hashable {
    let id: String
    let tag: String
    let detail: String

    init(capability: NPCCapabilityFact) {
        let tag = capability.label.cleanDisplayText
        self.tag = tag
        detail = Self.capabilityDetail(label: tag, category: capability.category, notes: capability.notes)
        id = "\(tag)-\(detail)"
    }

    init(rawText: String, kind: StarHookKind) {
        let cleaned = rawText.cleanDisplayText
        tag = Self.tag(from: cleaned, kind: kind)
        detail = Self.detail(from: cleaned, tag: tag, kind: kind)
        id = "\(tag)-\(detail)"
    }

    private static func capabilityDetail(label: String, category: String, notes: String?) -> String {
        if let notes = notes?.cleanDisplayText, !notes.isEmpty {
            return notes.hasSuffix("。") ? notes : "\(notes)。"
        }
        let categoryText = category.capabilityCategoryDetail
        if categoryText.contains("此长处") {
            return categoryText
        }
        return categoryText.replacingOccurrences(of: "可", with: "适合", options: [], range: categoryText.startIndex..<categoryText.index(after: categoryText.startIndex))
    }

    private static func tag(from text: String, kind: StarHookKind) -> String {
        let compact = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let firstClause = compact.components(separatedBy: CharacterSet(charactersIn: "，,；;。:：")).first ?? compact
        if firstClause.count <= 6, !firstClause.isEmpty {
            return firstClause
        }

        switch kind {
        case .positive:
            if compact.contains("内廷") || compact.contains("宫禁") || compact.contains("传旨") { return "内廷执行" }
            if compact.contains("军") || compact.contains("边") || compact.contains("战") { return "军务可托" }
            if compact.contains("财") || compact.contains("饷") || compact.contains("税") { return "财计可用" }
            if compact.contains("制度") || compact.contains("名分") || compact.contains("票拟") { return "制度名分" }
            if compact.contains("舆论") || compact.contains("清流") || compact.contains("声望") { return "舆论声望" }
            return firstClause.shortenedTag(fallback: "可用之处")
        case .risk:
            if compact.contains("旧") || compact.contains("牵连") || compact.contains("党") { return "旧网牵连" }
            if compact.contains("越权") || compact.contains("专擅") || compact.contains("权") { return "越权生疑" }
            if compact.contains("怨") || compact.contains("仇") || compact.contains("反噬") { return "积怨反噬" }
            if compact.contains("军") || compact.contains("边") || compact.contains("兵") { return "军务掣肘" }
            if compact.contains("名节") || compact.contains("清议") || compact.contains("舆论") { return "清议所限" }
            return firstClause.shortenedTag(fallback: "忌处未明")
        }
    }

    private static func detail(from text: String, tag: String, kind: StarHookKind) -> String {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleaned.isEmpty || cleaned == tag {
            return "未详。"
        }
        var readable = cleaned
            .replacingOccurrences(of: "皇帝", with: "若")
            .replacingOccurrences(of: "时更愿意承担此路线", with: "，此人更肯担事")
            .replacingOccurrences(of: "时更愿意承担", with: "，此人更肯承担")
            .replacingOccurrences(of: "此路线", with: "此事")
            .replacingOccurrences(of: "可提升", with: "并能增益")
            .replacingOccurrences(of: "会转为", with: "容易转成")
            .replacingOccurrences(of: "会扩大", with: "容易扩大")

        if kind == .risk, readable.hasPrefix("若") == false {
            readable = "若\(readable)"
        }
        return readable.hasSuffix("。") ? readable : "\(readable)。"
    }
}

private struct StarProfile: Identifiable {
    let core: NPCCoreRecord
    let rank: NPCRankTitleRecord?
    let start: NPCStart1628PositionRecord?
    let biography: NPCHistoricalBiographyRecord?
    let arc: NPCBiographyArcRecord?
    let tiangang: NPCTiangangProfileRecord?
    let mingshu: NPCMingshuProfileRecord?
    let xinpan: NPCXinpanSeedRecord?
    let social: NPCSocialCapitalRecord?
    let asset: NPCAssetRecord?
    let education: NPCEducationOriginRecord?
    let literacy: NPCCulturalLiteracyRecord?
    let literacyCatalog: NPCCulturalLiteracyCatalogRecord?
    let capabilities: NPCCapabilityFactsRecord?
    let relationships: [NPCRelationshipEdge]
    let officeContext: StarOfficeContext

    var id: String { core.npcID }
    var name: String { core.canonicalName }

    var identityKind: StarIdentityKind {
        StarIdentityKind(core: core, rank: rank)
    }

    var portraitCandidates: [String] {
        var candidates: [String] = []
        if let portraitAsset = asset?.portraitAsset, !portraitAsset.isEmpty {
            candidates.append(portraitAsset)
            candidates.append((portraitAsset as NSString).lastPathComponent)
            candidates.append(portraitAsset.replacingOccurrences(of: "minister_", with: ""))
        }
        candidates.append("\(name).png")
        candidates.append(name)

        var seen = Set<String>()
        return candidates.filter { value in
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, !seen.contains(trimmed) else { return false }
            seen.insert(trimmed)
            return true
        }
    }

    var titleLine: String {
        registerOfficeTitle
    }

    var captionLine: String {
        if isActiveOfficeHolder {
            return titleLine == "名位未详" ? statusText : "\(titleLine) · \(statusText)"
        }
        return registerOfficeTitle
    }

    var statusText: String {
        (start?.startStatus ?? .activeUnassigned).displayText
    }

    var isActiveOfficeHolder: Bool {
        (start?.startStatus ?? .activeUnassigned) == .activeInOffice
    }

    var registerInstitutionText: String {
        isActiveOfficeHolder ? officeContext.institutionName : "无现任官署"
    }

    var registerOfficeTitle: String {
        guard isActiveOfficeHolder else {
            return (start?.startStatus ?? .activeUnassigned).situationText
        }
        return firstKnown(officeContext.officeTitle, start?.startOfficeTitle, rank?.titleName, start?.environmentOfficeCanonicalTitle) ?? "名位未详"
    }

    var ageText: String {
        if let age = start?.ageAtStart {
            return "\(age)岁"
        }
        if let estimatedAge = start?.gameEstimatedAgeAtStart {
            return "约\(estimatedAge)岁"
        }
        if let birthYear = core.birthYear {
            return "\(birthYear)年生"
        }
        return "未详"
    }

    var lifeSpanText: String {
        let born = core.birthYear.map { "\($0)" } ?? "?"
        let died = core.historicalDeath.year.map { "\($0)" } ?? "?"
        if born == "?", died == "?" {
            return "生卒未详"
        }
        return "\(born)-\(died)"
    }

    var nativePlaceText: String {
        let pieces = [core.nativePlace.province, core.nativePlace.prefecture, core.nativePlace.county]
            .compactMap { $0 }
            .filter { !$0.isEmpty && $0 != "unknown" }
        return pieces.isEmpty ? "未详" : pieces.joined(separator: "·")
    }

    var baseRankText: String {
        if let code = start?.officialRankCode ?? rank?.officialRankCode {
            return code.displayText
        }
        if let rankApplicability = rank?.rankApplicability {
            return rankApplicability.displayText
        }
        return "未详"
    }

    var rankText: String {
        let base = baseRankText
        guard !isActiveOfficeHolder else { return base }
        if base == "未详" {
            return "享·旧衔未详"
        }
        return "享·\(base)"
    }

    var rankTagText: String {
        let base = baseRankText
        guard !isActiveOfficeHolder else { return base }
        switch base {
        case "品秩随差": return "享·随差"
        case "内廷位序": return "享·内序"
        case "位序称号": return "享·位序"
        case "外部非明制": return "外部"
        case "非朝廷承认": return "未承认"
        case "士民无品", "无正式品秩": return "无品"
        case "未详": return "享·未详"
        default: return "享·\(base)"
        }
    }

    var rankContextText: String {
        (start?.rankContext ?? rank?.rankContext)?.displayText ?? "未详"
    }

    var sexText: String {
        core.sexCategory.displayText
    }

    var powerText: String {
        switch core.powerID {
        case "ming": return "大明"
        case "houjin": return "后金"
        case "bandits": return "流寇"
        case "mongol": return "蒙古"
        case "korea": return "朝鲜"
        default: return core.powerID.isEmpty ? "未详" : core.powerID
        }
    }

    var educationText: String {
        literacyText
    }

    var literacyText: String {
        guard let literacy else { return "未详" }
        return literacy.label
    }

    var literacyAnchorText: String {
        literacyCatalog?.anchor.cleanDisplayText ?? ""
    }

    var hasPortrait: Bool {
        UIImage.starPortrait(named: portraitCandidates) != nil
    }

    var profileTags: [StarProfileTag] {
        [
            StarProfileTag(id: "power", text: powerText, color: powerColor),
            StarProfileTag(id: "rank", text: rankTagText, color: StarPalette.gold),
            StarProfileTag(id: "status", text: statusText, color: statusColor)
        ].filter { !$0.text.isEmpty && $0.text != "未详" }
    }

    var powerColor: Color {
        switch core.powerID {
        case "ming": return StarPalette.indigo
        case "houjin": return StarPalette.teal
        case "bandits": return StarPalette.earth
        case "mongol": return StarPalette.iron
        case "korea": return StarPalette.gold
        default: return StarPalette.muted
        }
    }

    var statusColor: Color {
        switch start?.startStatus ?? .activeUnassigned {
        case .activeInOffice, .activeUnassigned, .candidate:
            return StarPalette.indigo
        case .idleHome, .retired, .offstage:
            return StarPalette.muted
        case .suspended, .dismissed, .imprisoned, .exiled:
            return StarPalette.cinnabar
        case .dead:
            return StarPalette.faint
        }
    }

    var registerIdentityText: String {
        "\(rankContextText) · \(sexText)"
    }

    var overviewText: String {
        if let text = arc?.publicBiography.cleanDisplayText, !text.isEmpty {
            return text
        }
        if let text = biography?.biographyText.cleanDisplayText, !text.isEmpty {
            return text
        }
        return "\(name)入列群星谱，名籍与事迹尚待补录。"
    }

    var historyText: String {
        if let text = biography?.biographyText.cleanDisplayText, !text.isEmpty {
            return text
        }
        if let text = arc?.publicBiography.cleanDisplayText, !text.isEmpty {
            return text
        }
        return "列传未详。"
    }

    var politicalValues: [NPCTiangangValue] {
        tiangang?.values.filter { $0.dimensionType == "political" } ?? []
    }

    var skillValues: [NPCTiangangValue] {
        tiangang?.values.filter { $0.dimensionType != "political" } ?? []
    }

    var uniqueTopRelationships: [NPCSocialCapitalTopRelationship] {
        var seen = Set<String>()
        return (social?.topRelationships ?? []).filter { relation in
            guard relation.otherNPCID != id else { return false }
            let key = "\(relation.otherNPCID)-\(relation.rawType)"
            guard !seen.contains(key) else { return false }
            seen.insert(key)
            return true
        }
        .prefix(8)
        .map { $0 }
    }

    var positiveHooks: [StarHookTag] {
        var hooks: [StarHookTag] = []
        if let capabilities {
            hooks.append(contentsOf: capabilities.capabilities.prefix(3).map { StarHookTag(capability: $0) })
        }
        hooks.append(contentsOf: (arc?.riseHooks ?? []).prefix(3).map { StarHookTag(rawText: $0, kind: .positive) })
        return hooks.uniqueByTag().prefix(3).map { $0 }
    }

    var riskHooks: [StarHookTag] {
        (arc?.riskHooks ?? []).prefix(3).map { StarHookTag(rawText: $0, kind: .risk) }.uniqueByTag().prefix(3).map { $0 }
    }
}

private struct StarTiangangRow: Identifiable {
    let value: NPCTiangangValue
    let dimension: TiangangDimension?
    let skillAnchor: NPCSkillAnchorCatalogRecord?

    var id: String { value.dimensionID }

    var group: String {
        dimension?.group ?? (isPolitical ? "政治坐标" : "所学诸艺")
    }

    var isPolitical: Bool {
        (dimension?.type ?? value.dimensionType) == "political"
    }

    var currentValue: Int {
        max(1, min(5, value.value))
    }

    var currentLabel: String {
        dimension?.labels[String(currentValue)] ?? value.label
    }

    var currentExplanation: String {
        if let anchor = skillAnchor?.anchors.first(where: { $0.level == currentValue }) {
            return anchor.description
        }
        if let explanation = dimension?.labelExplanations[String(currentValue)], !explanation.isEmpty {
            return explanation
        }
        return value.label
    }

    var ladderLabels: [String] {
        (1...5).map { index in
            if let anchor = skillAnchor?.anchors.first(where: { $0.level == index }) {
                return anchor.label
            }
            return dimension?.labels[String(index)] ?? "\(index)"
        }
    }
}

private struct StarTiangangGroup: Identifiable {
    let name: String
    let rows: [StarTiangangRow]

    var id: String { name }

    var summary: String {
        let highlighted = rows
            .filter { $0.currentValue == 1 || $0.currentValue == 5 }
            .prefix(2)
            .map { "\($0.value.dimensionName)偏「\($0.currentLabel)」" }
        if !highlighted.isEmpty {
            return highlighted.joined(separator: "，")
        }
        let first = rows.prefix(2).map { "\($0.value.dimensionName)「\($0.currentLabel)」" }
        return first.isEmpty ? "未详" : first.joined(separator: "，")
    }
}

private enum StarIdentityKind: String, CaseIterable {
    case outerCivil
    case outerMilitary
    case innerEunuch
    case palace
    case external
    case rebel
    case nobility
    case civilian

    init(core: NPCCoreRecord, rank: NPCRankTitleRecord?) {
        let identity = core.identityType
        let power = core.powerID
        if identity.contains("rebel") || power == "bandits" {
            self = .rebel
        } else if power != "ming" {
            self = .external
        } else if core.sexCategory == .eunuch || identity.contains("eunuch") {
            self = .innerEunuch
        } else if identity.contains("harem") || identity.contains("female") || rank?.rankContext == .haremTitle || rank?.rankContext == .femaleOfficial {
            self = .palace
        } else if identity.contains("military") || rank?.rankContext == .outerMilitary {
            self = .outerMilitary
        } else if identity.contains("nobility") || rank?.rankContext == .nobility {
            self = .nobility
        } else if identity.contains("civilian") || rank?.rankContext == .civilian {
            self = .civilian
        } else {
            self = .outerCivil
        }
    }

    var displayText: String {
        switch self {
        case .outerCivil: return "外朝"
        case .outerMilitary: return "武臣"
        case .innerEunuch: return "内廷"
        case .palace: return "宫闱"
        case .external: return "外部"
        case .rebel: return "流寇"
        case .nobility: return "宗爵"
        case .civilian: return "士民"
        }
    }

    var sealGlyph: String {
        switch self {
        case .outerCivil: return "臣"
        case .outerMilitary: return "戟"
        case .innerEunuch: return "宦"
        case .palace: return "宫"
        case .external: return "外"
        case .rebel: return "乱"
        case .nobility: return "爵"
        case .civilian: return "民"
        }
    }

    var tint: Color {
        switch self {
        case .outerCivil: return StarPalette.indigo
        case .outerMilitary: return StarPalette.iron
        case .innerEunuch: return StarPalette.cinnabar
        case .palace: return StarPalette.plum
        case .external: return StarPalette.teal
        case .rebel: return StarPalette.earth
        case .nobility: return StarPalette.gold
        case .civilian: return StarPalette.muted
        }
    }
}

private enum StarCatalogTab: String, CaseIterable, Identifiable {
    case overview
    case tiangang
    case mingshu
    case xinpan
    case network

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "总览"
        case .tiangang: return "天罡"
        case .mingshu: return "命数"
        case .xinpan: return "心盘"
        case .network: return "势网"
        }
    }

    var glyph: String {
        switch self {
        case .overview: return "籍"
        case .tiangang: return "罡"
        case .mingshu: return "命"
        case .xinpan: return "心"
        case .network: return "网"
        }
    }
}

private enum StarPalette {
    static let background = Color(red: 0.945, green: 0.934, blue: 0.895)
    static let paper = Color(red: 0.990, green: 0.976, blue: 0.925)
    static let palePaper = Color(red: 0.972, green: 0.952, blue: 0.890)
    static let ink = Color(red: 0.110, green: 0.105, blue: 0.090)
    static let muted = Color(red: 0.450, green: 0.415, blue: 0.335)
    static let faint = Color(red: 0.720, green: 0.660, blue: 0.520)
    static let line = Color(red: 0.770, green: 0.700, blue: 0.545)
    static let cinnabar = Color(red: 0.635, green: 0.145, blue: 0.105)
    static let indigo = Color(red: 0.145, green: 0.230, blue: 0.300)
    static let teal = Color(red: 0.125, green: 0.360, blue: 0.355)
    static let gold = Color(red: 0.610, green: 0.440, blue: 0.190)
    static let iron = Color(red: 0.255, green: 0.280, blue: 0.285)
    static let earth = Color(red: 0.495, green: 0.270, blue: 0.145)
    static let plum = Color(red: 0.505, green: 0.160, blue: 0.245)
}

private struct StarHeaderView: View {
    let totalCount: Int
    let visibleCount: Int
    let activeFilterCount: Int
    let onFilterTapped: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .lastTextBaseline, spacing: 10) {
                    Text("群星谱")
                        .font(.system(size: 34, weight: .black, design: .serif))
                        .foregroundStyle(StarPalette.ink)
                    Text(countText)
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .foregroundStyle(StarPalette.cinnabar)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .overlay(
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(StarPalette.cinnabar.opacity(0.55), lineWidth: 1)
                        )
                }
                Text("崇祯元年诸臣、内廷、边镇、宫闱与天下异势，皆录其名籍、列传与势网。")
                    .font(.system(size: 14, weight: .regular, design: .serif))
                    .foregroundStyle(StarPalette.muted)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            StarFilterButton(activeFilterCount: activeFilterCount, action: onFilterTapped)
        }
    }

    private var countText: String {
        activeFilterCount == 0 ? "\(totalCount) 人" : "\(visibleCount)/\(totalCount)"
    }
}

private struct StarFilterButton: View {
    let activeFilterCount: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topTrailing) {
                Text("筛")
                    .font(.system(size: 16, weight: .black, design: .serif))
                    .foregroundStyle(activeFilterCount > 0 ? StarPalette.paper : StarPalette.cinnabar)
                    .frame(width: 42, height: 42)
                    .background(activeFilterCount > 0 ? StarPalette.cinnabar : StarPalette.paper.opacity(0.72))
                    .overlay(
                        Rectangle()
                            .stroke(StarPalette.cinnabar.opacity(0.64), lineWidth: 1)
                    )
                if activeFilterCount > 0 {
                    Text("\(activeFilterCount)")
                        .font(.system(size: 10, weight: .black, design: .serif))
                        .foregroundStyle(StarPalette.cinnabar)
                        .frame(width: 18, height: 18)
                        .background(StarPalette.paper, in: Circle())
                        .overlay(Circle().stroke(StarPalette.cinnabar.opacity(0.5), lineWidth: 1))
                        .offset(x: 7, y: -7)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(activeFilterCount > 0 ? "筛选，已启用\(activeFilterCount)项" : "筛选")
    }
}

private struct StarFilterSheet: View {
    @Binding var filter: StarCatalogFilterState
    let options: StarCatalogFilterOptions
    let resetFilters: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    FilterSheetHeader(activeFilterCount: filter.activeCount)

                    FilterSection(title: "身份") {
                        FilterChipGrid {
                            ForEach(StarIdentityKind.allCases, id: \.self) { identity in
                                FilterSealChip(
                                    title: identity.displayText,
                                    selected: filter.identities.contains(identity),
                                    color: identity.tint
                                ) {
                                    filter.identities.toggleMembership(identity)
                                }
                            }
                        }
                    }

                    FilterSection(title: "势力") {
                        FilterChipGrid {
                            ForEach(options.powers) { power in
                                FilterSealChip(
                                    title: power.label,
                                    selected: filter.powerIDs.contains(power.id),
                                    color: StarPalette.indigo
                                ) {
                                    filter.powerIDs.toggleMembership(power.id)
                                }
                            }
                        }
                    }

                    FilterSection(title: "状态") {
                        FilterChipGrid {
                            ForEach(options.statuses, id: \.self) { status in
                                FilterSealChip(
                                    title: status.displayText,
                                    selected: filter.statuses.contains(status),
                                    color: StarPalette.cinnabar
                                ) {
                                    filter.statuses.toggleMembership(status)
                                }
                            }
                        }
                    }

                    FilterSection(title: "品秩") {
                        FilterChipGrid {
                            ForEach(StarRankBand.allCases) { band in
                                FilterSealChip(
                                    title: band.title,
                                    selected: filter.rankBands.contains(band),
                                    color: StarPalette.gold
                                ) {
                                    filter.rankBands.toggleMembership(band)
                                }
                            }
                        }
                    }

                    FilterSection(title: "文教") {
                        FilterChipGrid {
                            ForEach(options.literacyOptions) { option in
                                FilterSealChip(
                                    title: option.label,
                                    selected: filter.literacyLevels.contains(option.level),
                                    color: StarPalette.plum
                                ) {
                                    filter.literacyLevels.toggleMembership(option.level)
                                }
                            }
                        }
                    }

                    FilterSection(title: "立绘") {
                        FilterChipGrid {
                            ForEach(StarPortraitFilter.allCases) { portrait in
                                FilterSealChip(
                                    title: portrait.title,
                                    selected: filter.portrait == portrait,
                                    color: StarPalette.teal
                                ) {
                                    filter.portrait = portrait
                                }
                            }
                        }
                    }
                }
                .padding(16)
            }
            .background(StarPalette.background.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("清空") {
                        resetFilters()
                    }
                    .disabled(filter.activeCount == 0)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") {
                        dismiss()
                    }
                }
            }
        }
    }
}

private struct FilterSheetHeader: View {
    let activeFilterCount: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("筛检群星")
                .font(.system(size: 26, weight: .black, design: .serif))
                .foregroundStyle(StarPalette.ink)
            Text(activeFilterCount == 0 ? "按名籍、势力与开局状态筛阅人物。" : "已启用 \(activeFilterCount) 项筛检。")
                .font(.system(size: 13, weight: .regular, design: .serif))
                .foregroundStyle(StarPalette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }
}

private struct FilterSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.system(size: 16, weight: .black, design: .serif))
                .foregroundStyle(StarPalette.ink)
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(StarPalette.paper.opacity(0.86))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(StarPalette.line.opacity(0.65), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

private struct FilterChipGrid<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 78), spacing: 8)], alignment: .leading, spacing: 8) {
            content
        }
    }
}

private struct FilterSealChip: View {
    let title: String
    let selected: Bool
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .black, design: .serif))
                .foregroundStyle(selected ? StarPalette.paper : color)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .padding(.horizontal, 8)
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity)
                .background(selected ? color.opacity(0.92) : StarPalette.palePaper.opacity(0.88))
                .overlay(
                    RoundedRectangle(cornerRadius: 3)
                        .stroke(color.opacity(selected ? 0.72 : 0.32), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 3))
        }
        .buttonStyle(.plain)
    }
}

private struct FilteredStarEmptyState: View {
    let resetFilters: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 9) {
                Text("空")
                    .font(.system(size: 14, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.cinnabar)
                    .frame(width: 28, height: 28)
                    .overlay(Rectangle().stroke(StarPalette.cinnabar.opacity(0.58), lineWidth: 1))
                Text("此筛无入册者")
                    .font(.system(size: 22, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
            }
            Text("放宽身份、状态或文教条件，可继续翻检群星谱。")
                .classicBody()
                .foregroundStyle(StarPalette.muted)
            Button(action: resetFilters) {
                Text("撤去筛检")
                    .font(.system(size: 14, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.cinnabar)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .overlay(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(StarPalette.cinnabar.opacity(0.55), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(StarPalette.paper.opacity(0.88))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(StarPalette.line.opacity(0.7), lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

private struct StarPortraitPager: View {
    @ObservedObject var store: StarCatalogStore
    @State private var isShowingFullPortrait = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            TabView(selection: $store.selectedNPCID) {
                ForEach(store.filteredProfiles) { profile in
                    StarCharacterStage(
                        profile: profile,
                        isShowingFullPortrait: isShowingFullPortrait,
                        showFullPortrait: {
                            withAnimation(.easeOut(duration: 0.22)) {
                                isShowingFullPortrait = true
                            }
                        },
                        restorePortrait: {
                            withAnimation(.easeOut(duration: 0.22)) {
                                isShowingFullPortrait = false
                            }
                        }
                    )
                    .tag(profile.id)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .frame(height: isShowingFullPortrait ? 570 : 404)
            .clipped()
            .animation(.easeOut(duration: 0.22), value: store.selectedNPCID)
            .animation(.easeOut(duration: 0.22), value: isShowingFullPortrait)
            .onChange(of: store.selectedNPCID) { _, _ in
                isShowingFullPortrait = false
            }

            Text("\(store.selectedIndex + 1)/\(max(store.filteredProfiles.count, 1))")
                .font(.system(size: 12, weight: .heavy, design: .serif))
                .foregroundStyle(StarPalette.muted)
                .padding(.horizontal, 9)
                .padding(.vertical, 5)
                .background(StarPalette.paper.opacity(0.86), in: Capsule())
                .overlay(
                    Capsule()
                        .stroke(StarPalette.line.opacity(0.4), lineWidth: 1)
                )
                .padding(.top, 8)
                .padding(.trailing, 16)
        }
    }
}

private struct StarCharacterStage: View {
    let profile: StarProfile
    let isShowingFullPortrait: Bool
    let showFullPortrait: () -> Void
    let restorePortrait: () -> Void

    var body: some View {
        Group {
            if isShowingFullPortrait {
                FullPortraitStage(profile: profile, restorePortrait: restorePortrait)
            } else {
                VStack(spacing: 0) {
                    PortraitFocusWindow(profile: profile, showFullPortrait: showFullPortrait)
                    StarCharacterCaption(profile: profile)
                        .frame(height: 128, alignment: .top)
                        .clipped()
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct PortraitFocusWindow: View {
    let profile: StarProfile
    let showFullPortrait: () -> Void

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                StageBackground(profile: profile)

                let portraitWidth = min(proxy.size.width * 1.34, 620)
                let portraitHeight = portraitWidth * 1.5
                PortraitImage(profile: profile, contentMode: .fill)
                    .frame(width: portraitWidth, height: portraitHeight)
                    .position(x: proxy.size.width / 2, y: portraitHeight * 0.45)
                    .shadow(color: Color.black.opacity(0.17), radius: 12, x: 0, y: 10)

                LinearGradient(
                    colors: [
                        StarPalette.background.opacity(0),
                        StarPalette.background.opacity(0.20)
                    ],
                    startPoint: .center,
                    endPoint: .bottom
                )
                .allowsHitTesting(false)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .contentShape(Rectangle())
            .onTapGesture(count: 2, perform: showFullPortrait)
        }
        .frame(height: 276)
    }
}

private struct FullPortraitStage: View {
    let profile: StarProfile
    let restorePortrait: () -> Void

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                StageBackground(profile: profile)

                PortraitImage(profile: profile, contentMode: .fit)
                    .frame(width: min(proxy.size.width * 0.96, 430), height: proxy.size.height - 24)
                    .shadow(color: Color.black.opacity(0.24), radius: 18, x: 0, y: 14)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .contentShape(Rectangle())
            .onTapGesture(perform: restorePortrait)
        }
    }
}

private struct StageBackground: View {
    let profile: StarProfile

    var body: some View {
        LinearGradient(
            colors: [
                profile.identityKind.tint.opacity(0.16),
                StarPalette.palePaper,
                StarPalette.background
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .overlay(alignment: .topLeading) {
            Text(profile.identityKind.sealGlyph)
                .font(.system(size: 118, weight: .black, design: .serif))
                .foregroundStyle(profile.identityKind.tint.opacity(0.06))
                .padding(.leading, 22)
                .padding(.top, 10)
        }
        .overlay {
            IdentityBackdropTexture(identity: profile.identityKind)
        }
        .overlay {
            PortraitImage(profile: profile, contentMode: .fill)
                .scaleEffect(1.9)
                .offset(y: 54)
                .blur(radius: 22)
                .opacity(UIImage.starPortrait(named: profile.portraitCandidates) == nil ? 0 : 0.09)
        }
    }
}

private struct IdentityBackdropTexture: View {
    let identity: StarIdentityKind

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                switch identity {
                case .outerCivil, .civilian:
                    civilLedgerTexture(size: proxy.size)
                case .outerMilitary:
                    militaryBannerTexture(size: proxy.size)
                case .innerEunuch:
                    palaceLatticeTexture(size: proxy.size)
                case .palace, .nobility:
                    wovenPalaceTexture(size: proxy.size)
                case .external:
                    frontierTexture(size: proxy.size)
                case .rebel:
                    roughPaperTexture(size: proxy.size)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .opacity(0.48)
        }
        .allowsHitTesting(false)
    }

    private func civilLedgerTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<9, id: \.self) { index in
                Rectangle()
                    .fill(identity.tint.opacity(index.isMultiple(of: 3) ? 0.075 : 0.035))
                    .frame(height: 1)
                    .offset(y: CGFloat(index) * size.height / 8 - size.height / 2)
            }
            ForEach(0..<5, id: \.self) { index in
                Rectangle()
                    .fill(identity.tint.opacity(0.035))
                    .frame(width: 1)
                    .offset(x: CGFloat(index) * size.width / 4 - size.width / 2)
            }
        }
    }

    private func militaryBannerTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<18, id: \.self) { index in
                Path { path in
                    let startX = CGFloat(index) * 34 - 150
                    path.move(to: CGPoint(x: startX, y: -20))
                    path.addLine(to: CGPoint(x: startX + size.height * 0.62, y: size.height + 20))
                }
                .stroke(identity.tint.opacity(0.05), lineWidth: index.isMultiple(of: 4) ? 2 : 1)
            }
        }
    }

    private func palaceLatticeTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<8, id: \.self) { index in
                Rectangle()
                    .fill(identity.tint.opacity(0.045))
                    .frame(height: 1)
                    .offset(y: CGFloat(index) * size.height / 7 - size.height / 2)
            }
            ForEach(0..<8, id: \.self) { index in
                Rectangle()
                    .fill(identity.tint.opacity(0.045))
                    .frame(width: 1)
                    .offset(x: CGFloat(index) * size.width / 7 - size.width / 2)
            }
        }
    }

    private func wovenPalaceTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<16, id: \.self) { index in
                Circle()
                    .stroke(identity.tint.opacity(0.045), lineWidth: 1)
                    .frame(width: 58, height: 58)
                    .offset(
                        x: CGFloat(index % 4) * size.width / 3 - size.width / 2,
                        y: CGFloat(index / 4) * size.height / 3 - size.height / 2
                    )
            }
        }
    }

    private func frontierTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<6, id: \.self) { index in
                Path { path in
                    let y = CGFloat(index) * size.height / 5
                    path.move(to: CGPoint(x: -20, y: y))
                    path.addQuadCurve(
                        to: CGPoint(x: size.width + 20, y: y + CGFloat(index % 2 == 0 ? 18 : -18)),
                        control: CGPoint(x: size.width * 0.45, y: y - 34)
                    )
                }
                .stroke(identity.tint.opacity(0.055), lineWidth: 1)
            }
        }
    }

    private func roughPaperTexture(size: CGSize) -> some View {
        ZStack {
            ForEach(0..<14, id: \.self) { index in
                Path { path in
                    let y = CGFloat(index) * size.height / 13
                    path.move(to: CGPoint(x: -12, y: y))
                    path.addLine(to: CGPoint(x: size.width * 0.38, y: y + CGFloat((index % 3) - 1) * 8))
                    path.addLine(to: CGPoint(x: size.width + 12, y: y + CGFloat((index % 2) * 10)))
                }
                .stroke(identity.tint.opacity(index.isMultiple(of: 4) ? 0.075 : 0.035), lineWidth: 1)
            }
        }
    }
}

private struct StarCharacterCaption: View {
    let profile: StarProfile

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(profile.name)
                        .font(.system(size: 32, weight: .black, design: .serif))
                        .foregroundStyle(StarPalette.ink)
                        .minimumScaleFactor(0.72)
                        .lineLimit(1)
                    Text(profile.captionLine)
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .foregroundStyle(profile.identityKind.tint)
                        .lineLimit(1)
                        .minimumScaleFactor(0.72)
                }
                Spacer(minLength: 8)
                ProfileTagStrip(tags: profile.profileTags)
                    .frame(width: 176, alignment: .trailing)
            }

            Text(profile.overviewText)
                .font(.system(size: 14, weight: .regular, design: .serif))
                .foregroundStyle(StarPalette.ink)
                .lineSpacing(3)
                .lineLimit(2)
        }
        .padding(.horizontal, 16)
        .padding(.top, 11)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(StarPalette.paper.opacity(0.90))
        .overlay(alignment: .top) {
            Rectangle()
                .fill(StarPalette.line.opacity(0.70))
                .frame(height: 1)
        }
    }
}

private struct PortraitImage: View {
    let profile: StarProfile
    let contentMode: ContentMode

    var body: some View {
        if let image = UIImage.starPortrait(named: profile.portraitCandidates) {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: contentMode)
        } else {
            IdentitySilhouette(identity: profile.identityKind)
        }
    }
}

private struct IdentitySilhouette: View {
    let identity: StarIdentityKind

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    identity.tint.opacity(0.20),
                    StarPalette.paper.opacity(0.98),
                    StarPalette.line.opacity(0.28)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            IdentityBackdropTexture(identity: identity)
            Text(identity.sealGlyph)
                .font(.system(size: 108, weight: .black, design: .serif))
                .foregroundStyle(identity.tint.opacity(0.42))
        }
    }
}

private struct StarTabRail: View {
    @Binding var selectedTab: StarCatalogTab

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(StarCatalogTab.allCases) { tab in
                    Button {
                        withAnimation(.easeOut(duration: 0.18)) {
                            selectedTab = tab
                        }
                    } label: {
                        VStack(spacing: 6) {
                            Text(tab.title)
                                .font(.system(size: 15, weight: selectedTab == tab ? .black : .bold, design: .serif))
                            Rectangle()
                                .fill(selectedTab == tab ? StarPalette.cinnabar : StarPalette.line.opacity(0.32))
                                .frame(height: selectedTab == tab ? 3 : 1)
                        }
                        .foregroundStyle(selectedTab == tab ? StarPalette.cinnabar : StarPalette.ink)
                        .padding(.horizontal, 13)
                        .padding(.vertical, 8)
                        .background(StarPalette.paper.opacity(selectedTab == tab ? 0.82 : 0.48))
                        .overlay(
                            RoundedRectangle(cornerRadius: 4)
                                .stroke(selectedTab == tab ? StarPalette.cinnabar.opacity(0.36) : StarPalette.line.opacity(0.36), lineWidth: 1)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 2)
        }
    }
}

private struct StarTabPage: View {
    let profile: StarProfile
    let selectedTab: StarCatalogTab
    let nameLookup: (String) -> String
    let tiangangDimensionsByID: [String: TiangangDimension]
    let tiangangGroups: [String]
    let skillAnchorsByDimensionID: [String: NPCSkillAnchorCatalogRecord]

    var body: some View {
        Group {
            switch selectedTab {
            case .overview:
                OverviewPage(profile: profile)
            case .tiangang:
                TiangangPage(
                    profile: profile,
                    dimensionsByID: tiangangDimensionsByID,
                    groupOrder: tiangangGroups,
                    skillAnchorsByDimensionID: skillAnchorsByDimensionID
                )
            case .mingshu:
                MingshuPage(profile: profile)
            case .xinpan:
                XinpanPage(profile: profile)
            case .network:
                NetworkPage(profile: profile, nameLookup: nameLookup)
            }
        }
    }
}

private struct OverviewPage: View {
    let profile: StarProfile

    var body: some View {
        VStack(spacing: 12) {
            ParchmentSection(title: "名籍", seal: "籍") {
                OverviewRegisterSection(profile: profile)
            }

            ParchmentSection(title: "列传", seal: "传") {
                Text(profile.historyText)
                    .classicBody()
            }

            if !profile.positiveHooks.isEmpty || !profile.riskHooks.isEmpty {
                ParchmentSection(title: "可用与忌处", seal: "策") {
                    HookTagSection(title: "可用", hooks: profile.positiveHooks, color: StarPalette.indigo)
                    ThinDivider()
                    HookTagSection(title: "忌处", hooks: profile.riskHooks, color: StarPalette.cinnabar)
                }
            }
        }
    }
}

private struct OverviewRegisterSection: View {
    let profile: StarProfile

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            FactGrid(items: [
                FactItem("年岁", profile.ageText),
                FactItem("籍贯", profile.nativePlaceText),
                FactItem("身份", profile.registerIdentityText),
                FactItem("官署", profile.registerInstitutionText),
                FactItem("职名", profile.registerOfficeTitle),
                FactItem("品秩", profile.rankText),
                FactItem("状态", profile.statusText),
                FactItem("文教", profile.educationText)
            ])
        }
    }
}

private struct RegisterAnnotation: View {
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(title)
                .font(.system(size: 12, weight: .black, design: .serif))
                .foregroundStyle(StarPalette.cinnabar)
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .overlay(Rectangle().stroke(StarPalette.cinnabar.opacity(0.45), lineWidth: 1))
            Text(text)
                .font(.system(size: 13, weight: .regular, design: .serif))
                .foregroundStyle(StarPalette.muted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
    }
}

private struct TiangangPage: View {
    let profile: StarProfile
    let dimensionsByID: [String: TiangangDimension]
    let groupOrder: [String]
    let skillAnchorsByDimensionID: [String: NPCSkillAnchorCatalogRecord]
    @State private var expandedGroups: Set<String> = []

    var body: some View {
        let groups = tiangangGroups

        VStack(spacing: 12) {
            TiangangSummarySlip(rows: tiangangRows)

            if groups.isEmpty {
                ParchmentSection(title: "天罡细目", seal: "罡") {
                    EmptySlip(text: "未详")
                }
            } else {
                ForEach(groups) { group in
                    TiangangDisclosureGroup(
                        group: group,
                        isExpanded: Binding(
                            get: { expandedGroups.contains(group.id) },
                            set: { isExpanded in
                                if isExpanded {
                                    expandedGroups.insert(group.id)
                                } else {
                                    expandedGroups.remove(group.id)
                                }
                            }
                        )
                    )
                }
            }
        }
        .onAppear {
            if expandedGroups.isEmpty {
                expandedGroups = Set(groups.prefix(2).map(\.id))
            }
        }
        .onChange(of: profile.id) { _, _ in
            expandedGroups = Set(tiangangGroups.prefix(2).map(\.id))
        }
    }

    private var tiangangRows: [StarTiangangRow] {
        (profile.tiangang?.values ?? []).map { value in
            StarTiangangRow(
                value: value,
                dimension: dimensionsByID[value.dimensionID],
                skillAnchor: skillAnchorsByDimensionID[value.dimensionID]
            )
        }
    }

    private var tiangangGroups: [StarTiangangGroup] {
        let rows = tiangangRows
        let grouped = Dictionary(grouping: rows, by: \.group)
        var used = Set<String>()
        var ordered: [StarTiangangGroup] = []

        for groupName in groupOrder {
            guard let rows = grouped[groupName], !rows.isEmpty else { continue }
            used.insert(groupName)
            ordered.append(StarTiangangGroup(name: groupName, rows: rows))
        }

        let leftovers = grouped.keys
            .filter { !used.contains($0) }
            .sorted()
            .compactMap { name -> StarTiangangGroup? in
                guard let rows = grouped[name] else { return nil }
                return StarTiangangGroup(name: name, rows: rows)
            }
        return ordered + leftovers
    }
}

private struct TiangangSummarySlip: View {
    let rows: [StarTiangangRow]

    var body: some View {
        ParchmentSection(title: "天罡摘录", seal: "摘") {
            VStack(alignment: .leading, spacing: 12) {
                SummaryCluster(
                    title: "鲜明立场",
                    rows: politicalHighlights,
                    emptyText: "立场平缓",
                    color: StarPalette.cinnabar
                )
                ThinDivider()
                SummaryCluster(
                    title: "强项",
                    rows: strongSkills,
                    emptyText: "强项未详",
                    color: StarPalette.indigo
                )
                ThinDivider()
                SummaryCluster(
                    title: "短处",
                    rows: weakSkills,
                    emptyText: "短处未详",
                    color: StarPalette.earth
                )
            }
        }
    }

    private var politicalHighlights: [StarTiangangRow] {
        rows
            .filter(\.isPolitical)
            .sorted { lhs, rhs in
                abs(lhs.currentValue - 3) == abs(rhs.currentValue - 3)
                    ? lhs.value.dimensionID < rhs.value.dimensionID
                    : abs(lhs.currentValue - 3) > abs(rhs.currentValue - 3)
            }
            .prefix(5)
            .map { $0 }
    }

    private var strongSkills: [StarTiangangRow] {
        rows
            .filter { !$0.isPolitical }
            .sorted { lhs, rhs in
                lhs.currentValue == rhs.currentValue
                    ? lhs.value.dimensionID < rhs.value.dimensionID
                    : lhs.currentValue > rhs.currentValue
            }
            .prefix(3)
            .map { $0 }
    }

    private var weakSkills: [StarTiangangRow] {
        rows
            .filter { !$0.isPolitical }
            .sorted { lhs, rhs in
                lhs.currentValue == rhs.currentValue
                    ? lhs.value.dimensionID < rhs.value.dimensionID
                    : lhs.currentValue < rhs.currentValue
            }
            .prefix(2)
            .map { $0 }
    }
}

private struct SummaryCluster: View {
    let title: String
    let rows: [StarTiangangRow]
    let emptyText: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 14, weight: .black, design: .serif))
                .foregroundStyle(color)

            if rows.isEmpty {
                Text(emptyText)
                    .classicBody()
                    .foregroundStyle(StarPalette.muted)
            } else {
                FlexibleFlow(items: rows.map { "\($0.value.dimensionName)｜\($0.currentLabel)" }) { item in
                    Text(item)
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .foregroundStyle(color)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(StarPalette.palePaper)
                        .overlay(
                            RoundedRectangle(cornerRadius: 3)
                                .stroke(color.opacity(0.28), lineWidth: 1)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 3))
                }
            }
        }
    }
}

private struct TiangangDisclosureGroup: View {
    let group: StarTiangangGroup
    @Binding var isExpanded: Bool

    var body: some View {
        ParchmentSection(title: group.name, seal: group.rows.first?.isPolitical == true ? "政" : "艺") {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(spacing: 0) {
                    ForEach(group.rows) { row in
                        TiangangDetailRow(row: row)
                        if row.id != group.rows.last?.id {
                            ThinDivider()
                        }
                    }
                }
                .padding(.top, 8)
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(group.summary)
                            .font(.system(size: 14, weight: .bold, design: .serif))
                            .foregroundStyle(StarPalette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(group.rows.count) 项")
                            .font(.system(size: 12, weight: .semibold, design: .serif))
                            .foregroundStyle(StarPalette.muted)
                    }
                    Spacer()
                    Text(isExpanded ? "收" : "展")
                        .font(.system(size: 13, weight: .black, design: .serif))
                        .foregroundStyle(StarPalette.cinnabar)
                        .frame(width: 28, height: 28)
                        .overlay(
                            Rectangle()
                                .stroke(StarPalette.cinnabar.opacity(0.45), lineWidth: 1)
                        )
                }
            }
            .tint(StarPalette.cinnabar)
        }
    }
}

private struct TiangangDetailRow: View {
    let row: StarTiangangRow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(row.value.dimensionName)
                    .font(.system(size: 17, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Text(row.currentLabel)
                    .font(.system(size: 14, weight: .bold, design: .serif))
                    .foregroundStyle(row.isPolitical ? StarPalette.cinnabar : StarPalette.indigo)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                Spacer()
                Text(row.isPolitical ? "坐标 \(row.currentValue)" : "第 \(row.currentValue) 阶")
                    .font(.system(size: 12, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.muted)
            }

            if row.isPolitical {
                PoliticalStanceLadder(row: row)
            } else {
                SkillAnchorLadder(row: row)
            }

            Text(row.currentExplanation.cleanDisplayText)
                .font(.system(size: 13, weight: .regular, design: .serif))
                .foregroundStyle(StarPalette.muted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 12)
    }
}

private struct MingshuPage: View {
    let profile: StarProfile

    var body: some View {
        VStack(spacing: 12) {
            ParchmentSection(title: "命数主签", seal: "命") {
                Text(profile.mingshu?.mingshuArchetype ?? "命数未详")
                    .font(.system(size: 24, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }

            ParchmentSection(title: "显著性情", seal: "性") {
                if let axes = profile.mingshu?.axes, !axes.isEmpty {
                    MingshuSignalGroup(title: "偏强", axes: highAxes(from: axes), color: StarPalette.indigo)
                    ThinDivider()
                    MingshuSignalGroup(title: "偏弱", axes: lowAxes(from: axes), color: StarPalette.earth)
                } else {
                    EmptySlip(text: "未详")
                }
            }

            ParchmentSection(title: "十维细目", seal: "目") {
                if let axes = profile.mingshu?.axes, !axes.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(axes) { axis in
                            MingshuAxisSlip(axis: axis)
                            if axis.id != axes.last?.id {
                                ThinDivider()
                            }
                        }
                    }
                } else {
                    EmptySlip(text: "命数未详")
                }
            }
        }
    }

    private func highAxes(from axes: [NPCMingshuAxis]) -> [NPCMingshuAxis] {
        axes.sorted { lhs, rhs in
            lhs.value == rhs.value ? lhs.axisID < rhs.axisID : lhs.value > rhs.value
        }
        .prefix(3)
        .map { $0 }
    }

    private func lowAxes(from axes: [NPCMingshuAxis]) -> [NPCMingshuAxis] {
        axes.sorted { lhs, rhs in
            lhs.value == rhs.value ? lhs.axisID < rhs.axisID : lhs.value < rhs.value
        }
        .prefix(3)
        .map { $0 }
    }
}

private struct MingshuSignalGroup: View {
    let title: String
    let axes: [NPCMingshuAxis]
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 14, weight: .black, design: .serif))
                .foregroundStyle(color)
            if axes.isEmpty {
                EmptySlip(text: "未详")
            } else {
                FlowTextChips(
                    items: axes.map { "\($0.axisName)：\($0.label)" },
                    color: color
                )
            }
        }
    }
}

private struct XinpanPage: View {
    let profile: StarProfile

    var body: some View {
        VStack(spacing: 12) {
            ParchmentSection(title: "心盘象限", seal: "心") {
                if let xinpan = profile.xinpan {
                    VStack(spacing: 14) {
                        HeartQuadrantChart(state: xinpan.initialState)
                        HeartTraceSealRow(state: xinpan.initialState)
                    }
                } else {
                    EmptySlip(text: "心盘未详")
                }
            }

            ParchmentSection(title: "所系关切", seal: "系") {
                let concerns = concernLabels(profile: profile)
                if concerns.isEmpty {
                    EmptySlip(text: "关切未详")
                } else {
                    FlowTextChips(items: concerns, color: StarPalette.indigo)
                }
            }
        }
    }

    private func concernLabels(profile: StarProfile) -> [String] {
        guard let concerns = profile.xinpan?.coreConcerns else { return [] }
        return concerns.prefix(6).map { concern in
            if let value = profile.tiangang?.values.first(where: { $0.dimensionID == concern.dimensionID }) {
                return "\(value.dimensionName)：\(value.label)"
            }
            return concern.dimensionID
        }
    }
}

private struct HeartQuadrantChart: View {
    let state: NPCXinpanInitialState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(state.quadrant.isEmpty ? quadrantName : state.quadrant)
                    .font(.system(size: 21, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Spacer()
                Text("道 \(String(format: "%.0f", state.daoHe))  ·  势 \(String(format: "%.0f", state.shiHe))")
                    .font(.system(size: 13, weight: .bold, design: .serif))
                    .foregroundStyle(StarPalette.muted)
            }

            GeometryReader { proxy in
                let width = proxy.size.width
                let height = proxy.size.height
                let centerX = width / 2
                let centerY = height / 2
                let x = centerX + normalized(state.shiHe) * width * 0.42
                let y = centerY - normalized(state.daoHe) * height * 0.38

                ZStack {
                    StarPalette.palePaper.opacity(0.72)

                    VStack {
                        HStack {
                            QuadrantName("同道异势")
                            Spacer()
                            QuadrantName("同道同势")
                        }
                        Spacer()
                        HStack {
                            QuadrantName("异道异势")
                            Spacer()
                            QuadrantName("异道同势")
                        }
                    }
                    .padding(10)

                    Path { path in
                        path.move(to: CGPoint(x: centerX, y: 0))
                        path.addLine(to: CGPoint(x: centerX, y: height))
                        path.move(to: CGPoint(x: 0, y: centerY))
                        path.addLine(to: CGPoint(x: width, y: centerY))
                    }
                    .stroke(StarPalette.line.opacity(0.72), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))

                    VStack {
                        Text("道同")
                            .axisLabel()
                        Spacer()
                        Text("道异")
                            .axisLabel()
                    }
                    .padding(.vertical, 4)

                    HStack {
                        Text("势离")
                            .axisLabel()
                        Spacer()
                        Text("势合")
                            .axisLabel()
                    }
                    .padding(.horizontal, 5)

                    Circle()
                        .fill(StarPalette.cinnabar)
                        .frame(width: 18, height: 18)
                        .overlay(
                            Circle()
                                .stroke(StarPalette.paper, lineWidth: 3)
                        )
                        .shadow(color: StarPalette.cinnabar.opacity(0.28), radius: 8, x: 0, y: 4)
                        .position(x: x, y: y)
                }
                .clipShape(RoundedRectangle(cornerRadius: 4))
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(StarPalette.line.opacity(0.68), lineWidth: 1)
                )
            }
            .frame(height: 238)
        }
    }

    private var quadrantName: String {
        if state.daoHe >= 0 && state.shiHe >= 0 { return "同道同势" }
        if state.daoHe >= 0 && state.shiHe < 0 { return "同道异势" }
        if state.daoHe < 0 && state.shiHe >= 0 { return "异道同势" }
        return "异道异势"
    }

    private func normalized(_ value: Double) -> CGFloat {
        CGFloat(max(-1, min(1, value / 100)))
    }
}

private struct QuadrantName: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.system(size: 12, weight: .bold, design: .serif))
            .foregroundStyle(StarPalette.faint)
    }
}

private struct HeartTraceSealRow: View {
    let state: NPCXinpanInitialState

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 8)], spacing: 8) {
            HeartTraceSeal(title: "畏惧", value: state.fear, maxValue: 80, color: StarPalette.cinnabar)
            HeartTraceSeal(title: "信任", value: state.trustCoeff, maxValue: 1.5, color: StarPalette.teal)
            HeartTraceSeal(title: "仇恨", value: state.hatred, maxValue: 80, color: StarPalette.earth)
        }
    }
}

private struct HeartTraceSeal: View {
    let title: String
    let value: Double
    let maxValue: Double
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.system(size: 13, weight: .black, design: .serif))
                Spacer()
                Text(formattedValue)
                    .font(.system(size: 12, weight: .bold, design: .serif))
            }
            .foregroundStyle(color)

            GeometryReader { proxy in
                let progress = max(0, min(1, value / maxValue))
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(StarPalette.line.opacity(0.35))
                    Rectangle()
                        .fill(color.opacity(0.78))
                        .frame(width: proxy.size.width * CGFloat(progress))
                }
            }
            .frame(height: 6)
        }
        .padding(9)
        .background(StarPalette.palePaper.opacity(0.76))
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(color.opacity(0.25), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private var formattedValue: String {
        maxValue <= 2 ? String(format: "%.2f", value) : String(format: "%.0f", value)
    }
}

private struct NetworkPage: View {
    let profile: StarProfile
    let nameLookup: (String) -> String

    var body: some View {
        VStack(spacing: 12) {
            ParchmentSection(title: "势网总目", seal: "网") {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(profile.social?.networkRole ?? "势网未详")
                            .font(.system(size: 22, weight: .black, design: .serif))
                            .foregroundStyle(StarPalette.ink)
                        Text("关系 \(profile.social?.edgeCounts.total ?? profile.relationships.count) 条")
                            .font(.system(size: 13, weight: .bold, design: .serif))
                            .foregroundStyle(StarPalette.muted)
                    }
                    Spacer()
                    SealBadge(text: profile.identityKind.displayText, color: profile.identityKind.tint)
                }

                if let metrics = profile.social?.metrics, !metrics.isEmpty {
                    ThinDivider()
                    VStack(spacing: 9) {
                        ForEach(metrics.prefix(8)) { metric in
                            MeasureBar(title: metric.metricName, value: metric.value, maxValue: 100, color: metric.metricID == "rivalry_pressure" ? StarPalette.cinnabar : StarPalette.indigo)
                        }
                    }
                }
            }

            ParchmentSection(title: "谱牒星链", seal: "星") {
                let relations = profile.uniqueTopRelationships
                if relations.isEmpty {
                    EmptySlip(text: "关系未详")
                } else {
                    VStack(spacing: 0) {
                        ForEach(relations) { relation in
                            RelationshipSlip(relation: relation, name: nameLookup(relation.otherNPCID))
                            if relation.id != relations.last?.id {
                                ThinDivider()
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct PoliticalStanceLadder: View {
    let row: StarTiangangRow

    var body: some View {
        HStack(spacing: 6) {
            ForEach(1...5, id: \.self) { index in
                let isCurrent = index == row.currentValue
                Text(row.ladderLabels[index - 1])
                    .font(.system(size: 10, weight: isCurrent ? .black : .semibold, design: .serif))
                    .foregroundStyle(isCurrent ? StarPalette.cinnabar : StarPalette.muted)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                    .minimumScaleFactor(0.72)
                    .frame(maxWidth: .infinity, minHeight: 36)
                    .padding(.vertical, 5)
                    .background(isCurrent ? StarPalette.cinnabar.opacity(0.08) : StarPalette.palePaper.opacity(0.58))
                    .overlay(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(isCurrent ? StarPalette.cinnabar.opacity(0.72) : StarPalette.line.opacity(0.45), lineWidth: isCurrent ? 1.2 : 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 3))
            }
        }
        .accessibilityLabel("\(row.value.dimensionName)：\(row.currentLabel)")
    }
}

private struct SkillAnchorLadder: View {
    let row: StarTiangangRow

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 4) {
                ForEach(1...5, id: \.self) { index in
                    let isReached = index <= row.currentValue
                    let isCurrent = index == row.currentValue
                    VStack(spacing: 3) {
                        Text("\(index)")
                            .font(.system(size: 11, weight: .black, design: .serif))
                        Text(row.ladderLabels[index - 1])
                            .font(.system(size: 10, weight: isCurrent ? .black : .semibold, design: .serif))
                            .lineLimit(1)
                            .minimumScaleFactor(0.65)
                    }
                    .foregroundStyle(isCurrent ? StarPalette.paper : isReached ? StarPalette.ink : StarPalette.muted)
                    .frame(maxWidth: .infinity, minHeight: 42)
                    .background(isReached ? (isCurrent ? StarPalette.indigo : StarPalette.ink.opacity(0.12)) : StarPalette.palePaper.opacity(0.58))
                    .overlay(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(isCurrent ? StarPalette.indigo : StarPalette.line.opacity(0.45), lineWidth: isCurrent ? 1.2 : 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 3))
                }
            }
        }
        .accessibilityLabel("\(row.value.dimensionName)：\(row.currentLabel)")
    }
}

private enum TiangangSlipStyle {
    case stance
    case skill
}

private struct TiangangValueSlip: View {
    let value: NPCTiangangValue
    let style: TiangangSlipStyle

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(value.dimensionName)
                    .font(.system(size: 17, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Text(value.label)
                    .font(.system(size: 14, weight: .bold, design: .serif))
                    .foregroundStyle(style == .stance ? StarPalette.cinnabar : StarPalette.indigo)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                Spacer()
                Text(style == .stance ? "坐标 \(value.value)" : "阶 \(value.value)")
                    .font(.system(size: 13, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.muted)
            }

            if style == .stance {
                FiveDotScale(value: value.value, activeColor: StarPalette.cinnabar, mode: .single)
            } else {
                FiveDotScale(value: value.value, activeColor: StarPalette.ink, mode: .cumulative)
            }
        }
        .padding(.vertical, 12)
    }
}

private struct MingshuAxisSlip: View {
    let axis: NPCMingshuAxis

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(axis.axisName)
                    .font(.system(size: 17, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Spacer(minLength: 10)
                Text(axis.label)
                    .font(.system(size: 14, weight: .bold, design: .serif))
                    .foregroundStyle(StarPalette.cinnabar)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
            FiveDotScale(value: axis.value, activeColor: StarPalette.cinnabar, mode: .single)
        }
        .padding(.vertical, 12)
    }
}

private struct RelationshipSlip: View {
    let relation: NPCSocialCapitalTopRelationship
    let name: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(spacing: 2) {
                Text("星")
                    .font(.system(size: 13, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.cinnabar)
                StarPips(score: relation.weightedScore)
            }
            .frame(width: 32)

            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text(name)
                        .font(.system(size: 17, weight: .black, design: .serif))
                        .foregroundStyle(StarPalette.ink)
                    Text(relation.rawType)
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .foregroundStyle(relation.relationshipKind.displayColor)
                    Spacer()
                }
                Text(relation.reason.playerFacingRelationReason)
                    .font(.system(size: 13, weight: .regular, design: .serif))
                    .foregroundStyle(StarPalette.muted)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 11)
    }
}

private struct StarPips: View {
    let score: Double

    var body: some View {
        VStack(spacing: 2) {
            ForEach(0..<max(1, min(5, Int((score / 20).rounded(.up)))), id: \.self) { _ in
                Circle()
                    .fill(StarPalette.line)
                    .frame(width: 4, height: 4)
            }
        }
    }
}

private enum FiveDotScaleMode {
    case single
    case cumulative
}

private struct FiveDotScale: View {
    let value: Int
    let activeColor: Color
    let mode: FiveDotScaleMode

    var body: some View {
        ZStack {
            Rectangle()
                .fill(StarPalette.line.opacity(0.55))
                .frame(height: 1)
                .padding(.horizontal, 8)

            HStack {
                ForEach(1...5, id: \.self) { index in
                    Circle()
                        .fill(isActive(index) ? activeColor : StarPalette.paper)
                        .overlay(
                            Circle()
                                .stroke(isActive(index) ? activeColor : StarPalette.line, lineWidth: isActive(index) ? 1.2 : 1)
                        )
                        .frame(width: index == value ? 18 : 13, height: index == value ? 18 : 13)
                    if index != 5 {
                        Spacer()
                    }
                }
            }
        }
        .frame(height: 22)
        .accessibilityLabel("刻度 \(value)")
    }

    private func isActive(_ index: Int) -> Bool {
        switch mode {
        case .single:
            return index == max(1, min(5, value))
        case .cumulative:
            return index <= max(1, min(5, value))
        }
    }
}

private struct BalanceScale: View {
    let title: String
    let value: Double
    let negativeLabel: String
    let positiveLabel: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(title)
                    .font(.system(size: 14, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Spacer()
                Text(String(format: "%.0f", value))
                    .font(.system(size: 13, weight: .bold, design: .serif))
                    .foregroundStyle(StarPalette.muted)
            }
            GeometryReader { proxy in
                let width = proxy.size.width
                let center = width / 2
                let normalized = max(-1, min(1, value / 100))
                let x = center + CGFloat(normalized) * width * 0.42

                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(StarPalette.line.opacity(0.55))
                        .frame(height: 1)
                        .position(x: center, y: 12)
                    Rectangle()
                        .fill(StarPalette.faint.opacity(0.75))
                        .frame(width: 1, height: 18)
                        .position(x: center, y: 12)
                    Circle()
                        .fill(color)
                        .frame(width: 18, height: 18)
                        .position(x: x, y: 12)
                }
            }
            .frame(height: 24)
            HStack {
                Text(negativeLabel)
                Spacer()
                Text(positiveLabel)
            }
            .font(.system(size: 12, weight: .semibold, design: .serif))
            .foregroundStyle(StarPalette.muted)
        }
    }
}

private struct MeasureBar: View {
    let title: String
    let value: Double
    let maxValue: Double
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title)
                    .font(.system(size: 14, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Spacer()
                Text(formattedValue)
                    .font(.system(size: 13, weight: .bold, design: .serif))
                    .foregroundStyle(StarPalette.muted)
            }
            GeometryReader { proxy in
                let width = proxy.size.width
                let progress = max(0, min(1, value / maxValue))
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(StarPalette.line.opacity(0.38))
                        .frame(height: 8)
                    Rectangle()
                        .fill(color.opacity(0.82))
                        .frame(width: width * CGFloat(progress), height: 8)
                }
            }
            .frame(height: 8)
        }
    }

    private var formattedValue: String {
        if maxValue <= 2 {
            return String(format: "%.2f", value)
        }
        return String(format: "%.0f", value)
    }
}

private struct ParchmentSection<Content: View>: View {
    let title: String
    let seal: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text(seal)
                    .font(.system(size: 13, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.cinnabar)
                    .frame(width: 25, height: 25)
                    .overlay(
                        Rectangle()
                            .stroke(StarPalette.cinnabar.opacity(0.62), lineWidth: 1)
                    )
                Text(title)
                    .font(.system(size: 20, weight: .black, design: .serif))
                    .foregroundStyle(StarPalette.ink)
                Spacer()
            }

            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(StarPalette.paper)
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(StarPalette.line.opacity(0.78), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

private struct FactItem: Identifiable {
    let id = UUID()
    let title: String
    let value: String

    init(_ title: String, _ value: String) {
        self.title = title
        self.value = value
    }
}

private struct FactGrid: View {
    let items: [FactItem]

    private let columns = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                        .font(.system(size: 12, weight: .bold, design: .serif))
                        .foregroundStyle(StarPalette.faint)
                    Text(item.value.isEmpty ? "未详" : item.value)
                        .font(.system(size: 15, weight: .bold, design: .serif))
                        .foregroundStyle(StarPalette.ink)
                        .lineLimit(2)
                        .minimumScaleFactor(0.72)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) {
                    Rectangle()
                        .fill(StarPalette.line.opacity(0.35))
                        .frame(height: 1)
                }
            }
        }
    }
}

private struct HookTagSection: View {
    let title: String
    let hooks: [StarHookTag]
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.system(size: 14, weight: .black, design: .serif))
                    .foregroundStyle(color)
                if hooks.isEmpty {
                    Text("未详")
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .foregroundStyle(StarPalette.faint)
                }
            }

            if !hooks.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(hooks.prefix(3)) { hook in
                        HStack(alignment: .top, spacing: 8) {
                            Text(hook.tag)
                                .font(.system(size: 12, weight: .black, design: .serif))
                                .foregroundStyle(color)
                                .lineLimit(1)
                                .minimumScaleFactor(0.72)
                                .frame(width: 74, alignment: .leading)
                                .padding(.vertical, 2)
                            Text(hook.detail)
                                .font(.system(size: 13, weight: .regular, design: .serif))
                                .foregroundStyle(StarPalette.ink)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.vertical, 5)
                        .padding(.horizontal, 8)
                        .background(StarPalette.palePaper.opacity(0.42))
                        .overlay(alignment: .leading) {
                            Rectangle()
                                .fill(color.opacity(0.52))
                                .frame(width: 2)
                        }
                    }
                }
            }
        }
    }
}

private struct HookList: View {
    let title: String
    let items: [String]
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 14, weight: .black, design: .serif))
                .foregroundStyle(color)
            if items.isEmpty {
                Text("未详")
                    .classicBody()
                    .foregroundStyle(StarPalette.muted)
            } else {
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .top, spacing: 8) {
                        Text("•")
                            .font(.system(size: 15, weight: .black, design: .serif))
                            .foregroundStyle(color)
                        Text(item.cleanDisplayText)
                            .classicBody()
                    }
                }
            }
        }
    }
}

private struct CapabilityLine: View {
    let capabilities: [NPCCapabilityFact]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("可见所长")
                .font(.system(size: 14, weight: .black, design: .serif))
                .foregroundStyle(StarPalette.indigo)
            FlowTextChips(items: capabilities.map(\.label), color: StarPalette.indigo)
        }
    }
}

private struct ProfileTagStrip: View {
    let tags: [StarProfileTag]

    var body: some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 50), spacing: 5)],
            alignment: .trailing,
            spacing: 5
        ) {
            ForEach(tags) { tag in
                Text(tag.text)
                    .font(.system(size: 11, weight: .black, design: .serif))
                    .foregroundStyle(tag.color)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity)
                    .background(StarPalette.palePaper.opacity(0.78))
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .stroke(tag.color.opacity(0.32), lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
            }
        }
    }
}

private struct FlowTextChips: View {
    let items: [String]
    let color: Color

    var body: some View {
        FlexibleFlow(items: items) { item in
            Text(item)
                .font(.system(size: 13, weight: .bold, design: .serif))
                .foregroundStyle(color)
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(StarPalette.palePaper)
                .overlay(
                    RoundedRectangle(cornerRadius: 3)
                        .stroke(color.opacity(0.28), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 3))
        }
    }
}

private struct FlexibleFlow<Data: RandomAccessCollection, Content: View>: View where Data.Element: Hashable {
    let items: Data
    let content: (Data.Element) -> Content

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 86), spacing: 7)], alignment: .leading, spacing: 7) {
            ForEach(Array(items), id: \.self) { item in
                content(item)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

private struct EraLine: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.system(size: 13, weight: .black, design: .serif))
                .foregroundStyle(StarPalette.cinnabar)
                .frame(width: 76, alignment: .leading)
            Text(value.isEmpty ? "未详" : value)
                .font(.system(size: 15, weight: .bold, design: .serif))
                .foregroundStyle(StarPalette.ink)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 7)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(StarPalette.line.opacity(0.35))
                .frame(height: 1)
        }
    }
}

private struct SealBadge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 13, weight: .black, design: .serif))
            .foregroundStyle(color)
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .stroke(color.opacity(0.55), lineWidth: 1)
            )
            .background(color.opacity(0.055))
    }
}

private struct ThinDivider: View {
    var body: some View {
        Rectangle()
            .fill(StarPalette.line.opacity(0.40))
            .frame(height: 1)
    }
}

private struct EmptySlip: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 14, weight: .bold, design: .serif))
            .foregroundStyle(StarPalette.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 10)
    }
}

private extension Text {
    func classicBody() -> some View {
        self
            .font(.system(size: 15, weight: .regular, design: .serif))
            .foregroundStyle(StarPalette.ink)
            .lineSpacing(4)
            .fixedSize(horizontal: false, vertical: true)
    }

    func axisLabel() -> some View {
        self
            .font(.system(size: 11, weight: .black, design: .serif))
            .foregroundStyle(StarPalette.muted)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(StarPalette.paper.opacity(0.82))
    }
}

private extension String {
    var cleanDisplayText: String {
        replacingOccurrences(of: "棋子", with: "人物")
            .replacingOccurrences(of: "游戏开局补网：", with: "")
            .replacingOccurrences(of: "此边服务开局棋盘与势网推演，不作史实断言。", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func ifEmpty(_ fallback: String) -> String {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? fallback : self
    }

    var displayExamDegree: String? {
        switch trimmingCharacters(in: .whitespacesAndNewlines) {
        case "jinshi": return "进士"
        case "juren": return "举人"
        case "shengyuan": return "生员"
        case "wuju": return "武举"
        case "unknown", "": return nil
        default: return nil
        }
    }

    var displayTrainingPath: String? {
        switch self {
        case "classical_bureaucracy": return "科举文臣"
        case "grand_secretariat_service": return "内阁历练"
        case "military_career", "military_administration": return "军旅历练"
        case "frontier_service": return "边镇历练"
        case "local_governance": return "地方政务"
        case "inner_court_training", "palace_service": return "内廷训练"
        case "censorial_service": return "台谏历练"
        case "fiscal_administration": return "财赋政务"
        case "ritual_and_diplomacy": return "礼外交涉"
        case "rebel_band_experience", "informal_military_network": return "草莽军伍"
        case "foreign_power_service": return "外部政权"
        case "palace_life": return "宫闱礼训"
        case "native_chieftain_service", "frontier_military_household": return "土司边务"
        case "technical_administration", "technical_studies", "western_learning": return "技艺实学"
        case "military_police_service", "secret_police_network", "investigation": return "侦缉查办"
        case "personnel_administration": return "铨选人事"
        case "hanlin_service": return "翰林馆阁"
        case "military_exam_origin": return "武科出身"
        case "unassigned_or_specialist_background": return "专门历练"
        default: return nil
        }
    }

    var capabilityCategoryDetail: String {
        switch self {
        case "court_administration":
            return "可托付章奏、名分与衙署流转。"
        case "inner_court":
            return "可承接宫禁传递、内廷执行与近侍沟通。"
        case "network_mobilization":
            return "可借其声望人脉，聚合助力或缓和清议。"
        case "political_tactics":
            return "可用于试探风向、折冲掣肘与权势安排。"
        case "culture_rhetoric":
            return "可用于文辞、礼制与名声经营。"
        case "military_command":
            return "可承担军务、边防或战阵执行。"
        case "fiscal_administration":
            return "可处理钱粮、税赋与军饷事务。"
        case "fiscal_economic":
            return "可处理钱粮亏空、税赋调度与军饷筹措。"
        case "military_warfare":
            return "可用于边务、兵事条议或战阵执行。"
        case "personal_combat":
            return "可用于近身护卫、搏杀冲阵或危险差遣。"
        case "security_intelligence":
            return "可用于侦缉、查访、密探与禁中风声。"
        case "technical_specialist":
            return "可用于器械、历算、医药或专门技艺。"
        case "religious_mystic":
            return "可用于方术、祭祀、民间信仰与安抚人心。"
        case "maritime_trade":
            return "可用于海路、商贸、人货转运与边缘渠道。"
        case "temperament_reputation":
            return "可借其声望与行事风格稳定局面。"
        case "uncategorized_specialty":
            return "此长处可作为专项差事的切入口。"
        default:
            return "此长处可作为临时差遣的参考。"
        }
    }

    func shortenedTag(fallback: String) -> String {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return fallback }
        if trimmed.count <= 6 { return trimmed }
        return String(trimmed.prefix(6))
    }

    var playerFacingRelationReason: String {
        if contains("游戏开局") || contains("不作史实断言") || contains("补网") {
            return "势网牵连，可作弱关系参考。"
        }
        return cleanDisplayText
    }
}

private extension Array where Element == StarHookTag {
    func uniqueByTag() -> [StarHookTag] {
        var seen = Set<String>()
        return filter { hook in
            guard !seen.contains(hook.tag) else { return false }
            seen.insert(hook.tag)
            return true
        }
    }
}

private extension Set {
    mutating func toggleMembership(_ value: Element) {
        if contains(value) {
            remove(value)
        } else {
            insert(value)
        }
    }
}

private extension UIImage {
    static func starPortrait(named candidates: [String]) -> UIImage? {
        for candidate in candidates {
            let bareName = (candidate as NSString).deletingPathExtension
            let pathName = "Portraits/\(bareName)"
            if let image = UIImage(named: candidate) ?? UIImage(named: bareName) ?? UIImage(named: pathName) {
                return image
            }
        }
        return nil
    }
}

private extension SexCategory {
    var displayText: String {
        switch self {
        case .male: return "男"
        case .female: return "女"
        case .eunuch: return "阉人"
        case .unknown: return "未详"
        }
    }
}

private extension NPCStatusCode {
    static let filterOrder: [NPCStatusCode] = [
        .activeInOffice,
        .activeUnassigned,
        .candidate,
        .idleHome,
        .offstage,
        .suspended,
        .dismissed,
        .retired,
        .imprisoned,
        .exiled,
        .dead
    ]

    var displayText: String {
        switch self {
        case .activeInOffice: return "在任"
        case .activeUnassigned: return "待用"
        case .idleHome: return "赋闲"
        case .candidate: return "候补"
        case .offstage: return "未登场"
        case .suspended: return "停职"
        case .dismissed: return "罢黜"
        case .retired: return "致仕"
        case .imprisoned: return "下狱"
        case .exiled: return "流放"
        case .dead: return "已故"
        }
    }

    var situationText: String {
        switch self {
        case .activeInOffice: return "在任"
        case .candidate: return "候补待铨"
        case .activeUnassigned: return "待命听用"
        case .idleHome: return "赋闲在籍"
        case .dismissed: return "罢黜中"
        case .suspended: return "停职听勘"
        case .retired: return "致仕归籍"
        case .imprisoned: return "下狱待讯"
        case .exiled: return "流放在外"
        case .offstage: return "未入局"
        case .dead: return "已故"
        }
    }
}

private extension OfficialRankCode {
    var grade: Int {
        switch self {
        case .zheng1, .cong1: return 1
        case .zheng2, .cong2: return 2
        case .zheng3, .cong3: return 3
        case .zheng4, .cong4: return 4
        case .zheng5, .cong5: return 5
        case .zheng6, .cong6: return 6
        case .zheng7, .cong7: return 7
        case .zheng8, .cong8: return 8
        case .zheng9, .cong9: return 9
        }
    }

    var displayText: String {
        switch self {
        case .zheng1: return "正一品"
        case .cong1: return "从一品"
        case .zheng2: return "正二品"
        case .cong2: return "从二品"
        case .zheng3: return "正三品"
        case .cong3: return "从三品"
        case .zheng4: return "正四品"
        case .cong4: return "从四品"
        case .zheng5: return "正五品"
        case .cong5: return "从五品"
        case .zheng6: return "正六品"
        case .cong6: return "从六品"
        case .zheng7: return "正七品"
        case .cong7: return "从七品"
        case .zheng8: return "正八品"
        case .cong8: return "从八品"
        case .zheng9: return "正九品"
        case .cong9: return "从九品"
        }
    }
}

private extension RankContext {
    var displayText: String {
        switch self {
        case .outerCivil: return "外朝文官"
        case .outerMilitary: return "外朝武职"
        case .innerEunuch: return "内廷宦官"
        case .femaleOfficial: return "女官"
        case .haremTitle: return "后妃位分"
        case .nobility: return "宗爵"
        case .civilian: return "士民"
        case .foreignTitle: return "外部称号"
        case .rebelTitle: return "流寇称号"
        }
    }
}

private extension RankApplicability {
    var displayText: String {
        switch self {
        case .ranked: return "有品秩"
        case .titleOrderOnly: return "位序称号"
        case .militaryCommandVariable: return "武职随任"
        case .missionOrDispatchVariable: return "品秩随差"
        case .innerEunuchOrder: return "内廷位序"
        case .civilianUnranked: return "士民无品"
        case .foreignNotMing: return "外部非明制"
        case .rebelOrUnrecognizedTitle: return "非朝廷承认"
        case .unofficialUnranked: return "无正式品秩"
        case .unknown: return "未详"
        }
    }
}

private extension RelationshipKind {
    var displayColor: Color {
        switch self {
        case .rivalry:
            return StarPalette.cinnabar
        case .patronClient, .mentorLine:
            return StarPalette.gold
        case .kinshipOrHousehold:
            return StarPalette.plum
        case .examCohort, .nativePlaceCohort, .affiliationPeer, .colleague:
            return StarPalette.indigo
        case .narrativeTie:
            return StarPalette.teal
        }
    }
}

private func firstKnown(_ values: String?...) -> String? {
    for value in values {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty,
              trimmed != "unknown",
              trimmed != "未详" else {
            continue
        }
        return trimmed
    }
    return nil
}
