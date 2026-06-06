import SwiftUI

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
                        VStack(alignment: .leading, spacing: 18) {
                            HeaderView()
                            OfficialPicker(store: store)
                            if let npc = store.selectedNPC {
                                OfficialSummaryCard(npc: npc)
                                TiangangGroupList(groups: store.groups)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
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
            Text("Ming iOS Tiangang Lab")
                .font(.system(.caption, design: .rounded).weight(.semibold))
                .foregroundStyle(LabPalette.cinnabar)
                .textCase(.uppercase)
                .tracking(1.2)
            Text("竖版天罡数值展示")
                .font(.system(size: 32, weight: .black, design: .serif))
                .foregroundStyle(LabPalette.ink)
            Text("开发测试版直接显示 36 维原值。此页只校验棋子底层数值，不做派生、不判强弱。")
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(LabPalette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct OfficialPicker: View {
    @ObservedObject var store: TiangangStore

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(store.selectedNames, id: \.self) { name in
                    Button {
                        store.selectedName = name
                    } label: {
                        Text(name)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(store.selectedName == name ? Color.white : LabPalette.ink)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            .background(
                                Capsule()
                                    .fill(store.selectedName == name ? LabPalette.indigo : LabPalette.panel)
                            )
                            .overlay(
                                Capsule()
                                    .stroke(store.selectedName == name ? LabPalette.indigo : LabPalette.line, lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 2)
        }
    }
}

private struct OfficialSummaryCard: View {
    let npc: TiangangNPC

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(npc.name)
                        .font(.system(size: 28, weight: .black, design: .serif))
                        .foregroundStyle(LabPalette.ink)
                    Text(npc.archetype)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(LabPalette.cinnabar)
                }
                Spacer()
                Text("36维")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(LabPalette.indigo)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(LabPalette.background, in: Capsule())
            }

            Divider().overlay(LabPalette.line)

            SummaryLine(title: "政治底色", bodyText: npc.politicalSummary)
            SummaryLine(title: "专业强项", bodyText: npc.professionalSummary)
            SummaryLine(title: "行为规则", bodyText: npc.behaviorRule)
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(LabPalette.panel)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(LabPalette.line, lineWidth: 1)
        )
    }
}

private struct SummaryLine: View {
    let title: String
    let bodyText: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(LabPalette.indigo)
            Text(bodyText)
                .font(.system(size: 14))
                .foregroundStyle(LabPalette.ink)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
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

private struct TiangangValueRowView: View {
    let row: TiangangValueRow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(row.dimension.symbol)
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(LabPalette.cinnabar)
                    .frame(width: 28, alignment: .leading)
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.dimension.name)
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(LabPalette.ink)
                    Text(row.currentLabel)
                        .font(.system(size: 13))
                        .foregroundStyle(LabPalette.muted)
                }
                Spacer(minLength: 10)
                Text("\(row.value) / 5")
                    .font(.system(size: 14, weight: .black, design: .rounded))
                    .foregroundStyle(valueColor(row.value))
                    .monospacedDigit()
            }
            FiveStepMeter(value: row.value, type: row.dimension.type)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
    }

    private func valueColor(_ value: Int) -> Color {
        switch value {
        case 1: return LabPalette.cinnabar
        case 2: return LabPalette.bronze
        case 4: return LabPalette.indigo
        case 5: return Color(red: 0.095, green: 0.300, blue: 0.260)
        default: return LabPalette.muted
        }
    }
}

private struct FiveStepMeter: View {
    let value: Int
    let type: String

    var body: some View {
        HStack(spacing: 5) {
            ForEach(1...5, id: \.self) { index in
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(index <= value ? fillColor : LabPalette.background)
                    .overlay(
                        RoundedRectangle(cornerRadius: 3, style: .continuous)
                            .stroke(LabPalette.line.opacity(0.8), lineWidth: 0.6)
                    )
                    .frame(height: 7)
            }
        }
        .accessibilityLabel("天罡数值 \(value) / 5")
    }

    private var fillColor: Color {
        type == "professional" ? LabPalette.indigo : LabPalette.cinnabar
    }
}

#Preview {
    TiangangRootView()
}
