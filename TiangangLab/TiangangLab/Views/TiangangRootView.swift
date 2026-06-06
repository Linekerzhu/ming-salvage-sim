import SwiftUI
import UIKit

struct TiangangRootView: View {
    @StateObject private var store = TiangangStore()

    var body: some View {
        NavigationStack {
            ZStack {
                LabPalette.background.ignoresSafeArea()
                if let error = store.loadError {
                    ContentUnavailableView("天罡数据未载入", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            HeaderView()
                                .padding(.horizontal, 16)
                                .padding(.top, 12)
                                .padding(.bottom, 8)
                            CharacterPager(store: store)
                                .padding(.bottom, 18)
                            if store.selectedNPC != nil {
                                TiangangGroupList(groups: store.groups)
                                    .padding(.horizontal, 16)
                            }
                        }
                        .padding(.bottom, 32)
                    }
                }
            }
            .navigationTitle("天罡棋库")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

private enum LabPalette {
    static let background = Color(red: 0.945, green: 0.938, blue: 0.912)
    static let panel = Color(red: 0.986, green: 0.976, blue: 0.944)
    static let ink = Color(red: 0.110, green: 0.120, blue: 0.120)
    static let muted = Color(red: 0.440, green: 0.420, blue: 0.360)
    static let line = Color(red: 0.800, green: 0.760, blue: 0.660)
    static let cinnabar = Color(red: 0.635, green: 0.145, blue: 0.120)
    static let indigo = Color(red: 0.160, green: 0.235, blue: 0.310)
    static let bronze = Color(red: 0.620, green: 0.430, blue: 0.210)
}

private struct HeaderView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("明廷官员册")
                .font(.system(size: 30, weight: .black, design: .serif))
                .foregroundStyle(LabPalette.ink)
            Text("内廷、外朝、边镇之人，各有立场与本事。")
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(LabPalette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct CharacterPager: View {
    @ObservedObject var store: TiangangStore
    @State private var isShowingFullPortrait = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            TabView(selection: $store.selectedName) {
                ForEach(store.selectedNames, id: \.self) { name in
                    if let npc = store.catalog?.npcs[name] {
                        CharacterStage(
                            npc: npc,
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
                            .tag(name)
                    }
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .frame(height: isShowingFullPortrait ? 560 : 432)
            .clipped()
            .animation(.easeOut(duration: 0.22), value: store.selectedName)
            .animation(.easeOut(duration: 0.22), value: isShowingFullPortrait)
            .onChange(of: store.selectedName) { _, _ in
                isShowingFullPortrait = false
            }

            Text(pageText)
                .font(.system(size: 12, weight: .heavy, design: .rounded))
                .foregroundStyle(LabPalette.muted)
                .padding(.horizontal, 9)
                .padding(.vertical, 5)
                .background(LabPalette.panel.opacity(0.78), in: Capsule())
                .padding(.top, 8)
                .padding(.trailing, 16)
        }
    }

    private var pageText: String {
        let index = store.selectedNames.firstIndex(of: store.selectedName).map { $0 + 1 } ?? 1
        return "\(index)/\(max(store.selectedNames.count, 1))"
    }
}

private struct CharacterStage: View {
    let npc: TiangangNPC
    let isShowingFullPortrait: Bool
    let showFullPortrait: () -> Void
    let restorePortrait: () -> Void

    var body: some View {
        Group {
            if isShowingFullPortrait {
                FullPortraitStage(npc: npc, restorePortrait: restorePortrait)
            } else {
                VStack(spacing: 0) {
                    PortraitFocusWindow(npc: npc, showFullPortrait: showFullPortrait)
                    CharacterCaption(npc: npc)
                        .frame(height: 156, alignment: .top)
                        .clipped()
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct PortraitFocusWindow: View {
    let npc: TiangangNPC
    let showFullPortrait: () -> Void

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                StageBackground(asset: npc.portraitAsset)

                let portraitWidth = min(proxy.size.width * 1.36, 620)
                let portraitHeight = portraitWidth * 1.5
                PortraitImage(asset: npc.portraitAsset, contentMode: .fill)
                    .frame(width: portraitWidth, height: portraitHeight)
                    .position(x: proxy.size.width / 2, y: portraitHeight * 0.45)
                    .shadow(color: Color.black.opacity(0.18), radius: 14, x: 0, y: 10)

                LinearGradient(
                    colors: [
                        LabPalette.background.opacity(0),
                        LabPalette.background.opacity(0.18)
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
    let npc: TiangangNPC
    let restorePortrait: () -> Void

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                StageBackground(asset: npc.portraitAsset)

                PortraitImage(asset: npc.portraitAsset, contentMode: .fit)
                    .frame(width: min(proxy.size.width * 0.95, 410), height: proxy.size.height - 22)
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
    let asset: String

    var body: some View {
        LinearGradient(
            colors: [
                Color(red: 0.910, green: 0.888, blue: 0.815),
                LabPalette.background,
                Color(red: 0.835, green: 0.802, blue: 0.704)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .overlay {
            PortraitImage(asset: asset, contentMode: .fill)
                .scaleEffect(1.9)
                .offset(y: 54)
                .blur(radius: 22)
                .opacity(0.10)
        }
    }
}

private struct CharacterCaption: View {
    let npc: TiangangNPC

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(npc.name)
                        .font(.system(size: 34, weight: .black, design: .serif))
                        .foregroundStyle(LabPalette.ink)
                    Text(npc.archetype)
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(LabPalette.cinnabar)
                }
                Spacer()
                Text("36维")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(LabPalette.indigo)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(LabPalette.background.opacity(0.74), in: Capsule())
            }

            Text(npc.portraitText.isEmpty ? npc.politicalSummary : npc.portraitText)
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(LabPalette.ink)
                .lineSpacing(4)
                .lineLimit(3)
        }
        .padding(.horizontal, 18)
        .padding(.top, 14)
        .padding(.bottom, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            Rectangle()
                .fill(LabPalette.panel.opacity(0.82))
                .background(.ultraThinMaterial)
        )
        .overlay(alignment: .top) {
            Rectangle()
                .fill(LabPalette.line.opacity(0.65))
                .frame(height: 1)
        }
    }
}

private struct PortraitImage: View {
    let asset: String
    let contentMode: ContentMode

    init(asset: String, contentMode: ContentMode = .fill) {
        self.asset = asset
        self.contentMode = contentMode
    }

    var body: some View {
        if let image = UIImage.tiangangPortrait(named: asset) {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: contentMode)
        } else {
            ZStack {
                LabPalette.indigo.opacity(0.88)
                Image(systemName: "person.fill")
                    .font(.system(size: 42, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.82))
            }
        }
    }
}

private extension UIImage {
    static func tiangangPortrait(named asset: String) -> UIImage? {
        let filename = URL(fileURLWithPath: asset).lastPathComponent
        let parts = filename.split(separator: ".", maxSplits: 1).map(String.init)
        let name = parts.first ?? filename
        let fileExtension = parts.count > 1 ? parts[1] : "png"
        let url = Bundle.main.url(forResource: name, withExtension: fileExtension)
            ?? Bundle.main.url(forResource: name, withExtension: fileExtension, subdirectory: "Portraits")
        guard let url else { return nil }
        return UIImage(contentsOfFile: url.path)
    }
}

private struct TiangangGroupList: View {
    let groups: [TiangangGroup]

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 14) {
            ForEach(groups) { group in
                TiangangGroupSection(group: group)
            }
        }
    }
}

private struct TiangangGroupSection: View {
    let group: TiangangGroup

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(group.name)
                    .font(.system(size: 18, weight: .black, design: .serif))
                    .foregroundStyle(LabPalette.ink)
                Spacer()
                Text("\(group.rows.count) 项")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(LabPalette.muted)
            }

            VStack(spacing: 0) {
                ForEach(group.rows) { row in
                    TiangangValueRowView(row: row)
                    if row.id != group.rows.last?.id {
                        Divider().overlay(LabPalette.line.opacity(0.7))
                    }
                }
            }
            .background(LabPalette.panel)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(LabPalette.line, lineWidth: 1)
            )
        }
    }
}

private enum TiangangGlyphs {
    static let map: [String: String] = [
        "d01": "鼎",
        "d02": "冕",
        "d03": "宫",
        "d04": "眼",
        "d05": "磨",
        "d06": "心",
        "d07": "结",
        "d08": "碑",
        "d09": "戟",
        "d10": "门",
        "d11": "义",
        "d12": "刃",
        "d13": "谏",
        "d14": "刑",
        "d15": "炉",
        "d16": "舆",
        "d17": "关",
        "d18": "星",
        "d19": "钱",
        "d20": "民",
        "d21": "简",
        "d22": "贯",
        "d23": "律",
        "d24": "笔",
        "d25": "旗",
        "d26": "鼓",
        "d27": "剑",
        "d28": "仓",
        "d29": "影",
        "d30": "谋",
        "d31": "狱",
        "d32": "印",
        "d33": "舌",
        "d34": "盘",
        "d35": "旌",
        "d36": "器",
    ]

    static func glyph(for id: String) -> String {
        map[id] ?? "印"
    }
}

private struct TiangangGlyphBadge: View {
    let glyph: String
    let isProfessional: Bool

    var body: some View {
        Text(glyph)
            .font(.system(size: 14, weight: .black, design: .serif))
            .foregroundStyle(isProfessional ? LabPalette.indigo : LabPalette.cinnabar)
            .frame(width: 28, height: 28)
            .background(
                Circle()
                    .fill(isProfessional ? LabPalette.indigo.opacity(0.08) : LabPalette.cinnabar.opacity(0.08))
            )
            .overlay(
                Circle()
                    .stroke(isProfessional ? LabPalette.indigo.opacity(0.38) : LabPalette.cinnabar.opacity(0.42), lineWidth: 1)
            )
    }
}

private struct TiangangValueRowView: View {
    let row: TiangangValueRow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                TiangangGlyphBadge(
                    glyph: TiangangGlyphs.glyph(for: row.dimension.id),
                    isProfessional: row.dimension.type == "professional"
                )
                .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 2) {
                    Text(row.dimension.name)
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(LabPalette.ink)
                    Text(row.currentExplanation)
                        .font(.system(size: 12))
                        .foregroundStyle(LabPalette.muted)
                        .lineSpacing(2)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 10)
                Text(valueText)
                    .font(.system(size: 14, weight: .black, design: .rounded))
                    .foregroundStyle(valueColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
                    .multilineTextAlignment(.trailing)
            }
            if row.dimension.type == "professional" {
                SkillMeter(value: row.value)
            } else {
                StanceScale(value: row.value, labels: row.dimension.labels, selectedLabel: row.currentLabel)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
    }

    private var valueText: String {
        row.dimension.type == "professional" ? "\(row.value) / 5" : row.currentLabel
    }

    private var valueColor: Color {
        row.dimension.type == "professional" ? skillColor(row.value) : LabPalette.indigo
    }

    private func skillColor(_ value: Int) -> Color {
        switch value {
        case 1: return LabPalette.cinnabar
        case 2: return LabPalette.bronze
        case 4: return LabPalette.indigo
        case 5: return Color(red: 0.095, green: 0.300, blue: 0.260)
        default: return LabPalette.muted
        }
    }
}

private struct SkillMeter: View {
    let value: Int

    var body: some View {
        HStack(spacing: 5) {
            ForEach(1...5, id: \.self) { index in
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(index <= value ? LabPalette.indigo : LabPalette.background)
                    .overlay(
                        RoundedRectangle(cornerRadius: 3, style: .continuous)
                            .stroke(LabPalette.line.opacity(0.8), lineWidth: 0.6)
                    )
                    .frame(height: 7)
            }
        }
        .accessibilityLabel("专业技能 \(value) / 5")
    }
}

private struct StanceScale: View {
    let value: Int
    let labels: [String: String]
    let selectedLabel: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            GeometryReader { geometry in
                let markerSize: CGFloat = 14
                let trackStart = markerSize / 2
                let trackEnd = geometry.size.width - markerSize / 2
                let step = (trackEnd - trackStart) / 4
                let midY = geometry.size.height / 2

                ZStack(alignment: .leading) {
                    Path { path in
                        path.move(to: CGPoint(x: trackStart, y: midY))
                        path.addLine(to: CGPoint(x: trackEnd, y: midY))
                    }
                    .stroke(LabPalette.line.opacity(0.72), lineWidth: 1)

                    ForEach(1...5, id: \.self) { index in
                        let isActive = index == value
                        Circle()
                            .fill(isActive ? LabPalette.cinnabar : LabPalette.panel)
                            .frame(width: isActive ? markerSize : 9, height: isActive ? markerSize : 9)
                            .overlay(
                                Circle()
                                    .stroke(isActive ? LabPalette.cinnabar : LabPalette.line, lineWidth: 1)
                            )
                            .position(x: trackStart + CGFloat(index - 1) * step, y: midY)
                    }
                }
            }
            .frame(height: 18)

            HStack(alignment: .top) {
                Text(labels["1"] ?? "一端")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(labels["5"] ?? "另一端")
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(LabPalette.muted)
        }
        .accessibilityLabel("价值立场 \(selectedLabel)")
    }
}

#Preview {
    TiangangRootView()
}
