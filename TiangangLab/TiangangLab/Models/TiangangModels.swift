import Foundation

struct TiangangCatalog: Decodable {
    let source: String
    let selectedNPCs: [String]
    let meta: TiangangMeta
    let npcs: [String: TiangangNPC]

    enum CodingKeys: String, CodingKey {
        case source
        case selectedNPCs = "selected_npcs"
        case meta
        case npcs
    }
}

struct TiangangMeta: Decodable {
    let version: String
    let source: String
    let hiddenByDefault: Bool
    let growthEnabled: Bool
    let dimensions: [TiangangDimension]

    enum CodingKeys: String, CodingKey {
        case version
        case source
        case hiddenByDefault = "hidden_by_default"
        case growthEnabled = "growth_enabled"
        case dimensions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try? container.decode(String.self, forKey: .version) {
            version = value
        } else if let value = try? container.decode(Int.self, forKey: .version) {
            version = String(value)
        } else {
            version = ""
        }
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? ""
        hiddenByDefault = try container.decodeIfPresent(Bool.self, forKey: .hiddenByDefault) ?? true
        growthEnabled = try container.decodeIfPresent(Bool.self, forKey: .growthEnabled) ?? false
        dimensions = try container.decode([TiangangDimension].self, forKey: .dimensions)
    }
}

struct TiangangDimension: Decodable, Identifiable, Hashable {
    let id: String
    let symbol: String
    let name: String
    let group: String
    let type: String
    let labels: [String: String]
}

struct TiangangNPC: Decodable {
    let name: String
    let hidden: Bool
    let archetype: String
    let values: [String: Int]
    let politicalSummary: String
    let professionalSummary: String
    let behaviorRule: String
    let aiUse: String

    enum CodingKeys: String, CodingKey {
        case name
        case hidden
        case archetype
        case values
        case politicalSummary = "political_summary"
        case professionalSummary = "professional_summary"
        case behaviorRule = "behavior_rule"
        case aiUse = "ai_use"
    }
}

struct TiangangValueRow: Identifiable, Hashable {
    let dimension: TiangangDimension
    let value: Int

    var id: String { dimension.id }

    var currentLabel: String {
        dimension.labels[String(value)] ?? "未标注"
    }
}

struct TiangangGroup: Identifiable, Hashable {
    let name: String
    let rows: [TiangangValueRow]

    var id: String { name }
}

@MainActor
final class TiangangStore: ObservableObject {
    @Published private(set) var catalog: TiangangCatalog?
    @Published private(set) var loadError: String?
    @Published var selectedName: String = ""

    init() {
        load()
    }

    var selectedNPC: TiangangNPC? {
        guard let catalog else { return nil }
        return catalog.npcs[selectedName]
    }

    var selectedNames: [String] {
        catalog?.selectedNPCs ?? []
    }

    var groups: [TiangangGroup] {
        guard let catalog, let npc = selectedNPC else { return [] }
        var orderedNames: [String] = []
        var grouped: [String: [TiangangValueRow]] = [:]

        for dimension in catalog.meta.dimensions {
            guard let value = npc.values[dimension.id] else { continue }
            if grouped[dimension.group] == nil {
                grouped[dimension.group] = []
                orderedNames.append(dimension.group)
            }
            grouped[dimension.group]?.append(TiangangValueRow(dimension: dimension, value: max(1, min(5, value))))
        }

        return orderedNames.map { TiangangGroup(name: $0, rows: grouped[$0] ?? []) }
    }

    private func load() {
        guard let url = Bundle.main.url(forResource: "tiangang_seed", withExtension: "json") else {
            loadError = "未找到 tiangang_seed.json"
            return
        }

        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(TiangangCatalog.self, from: data)
            catalog = decoded
            selectedName = decoded.selectedNPCs.first ?? ""
        } catch {
            loadError = error.localizedDescription
        }
    }
}
