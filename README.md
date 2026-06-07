# 崇祯模拟器 iOS

This orphan branch starts the iOS prototype from a blank tree.

The first prototype is deliberately small:

- SwiftUI portrait-only Tiangang viewer.
- Six official pieces: Han Kuang, Bi Ziyan, Cui Chengxiu, Cao Huachun, Yuan Chonghuan, and Zu Dashou.
- Swipeable official portraits are fixed at the top of the iOS UI.
- Full 36-dimension Tiangang values are visible in the development build, with value stances shown separately from professional skill bars.
- Tiangang dimension labels include player-facing explanations for every stance and skill level.
- No backend, no LLM, no Python/Web runtime dependency.

This branch now keeps only clean runtime data for the iOS prototype:

- `ChongzhenSimulator/Resources/NPCDatabase` contains final NPC game data.
- `ChongzhenSimulator/Resources/EnvironmentDatabase` contains final administrative, institution, office, rank, location, and eunuch-agency data.
- Build notebooks, bridge indices, and generator-only artifacts are intentionally excluded from the iOS runtime tree.

Open `ChongzhenSimulator/ChongzhenSimulator.xcodeproj` in Xcode and run the `ChongzhenSimulator` scheme on an iPhone simulator.
