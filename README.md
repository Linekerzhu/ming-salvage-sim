# 崇祯模拟器 iOS

This orphan branch starts the iOS prototype from a blank tree.

The first prototype is deliberately small:

- SwiftUI portrait-only Tiangang viewer.
- Six official pieces: Han Kuang, Bi Ziyan, Cui Chengxiu, Cao Huachun, Yuan Chonghuan, and Zu Dashou.
- Swipeable official portraits are fixed at the top of the iOS UI.
- Full 36-dimension Tiangang values are visible in the development build, with value stances shown separately from professional skill bars.
- Tiangang dimension labels include player-facing explanations for every stance and skill level.
- The current viewer works without backend, LLM, or Python/Web runtime dependencies; optional LLM service routes are scaffolded for future gameplay features.

This branch now keeps only clean runtime data for the iOS prototype:

- `ChongzhenSimulator/Resources/NPCDatabase` contains final NPC game data.
- `ChongzhenSimulator/Resources/EnvironmentDatabase` contains final administrative, institution, office, rank, location, and eunuch-agency data.
- Build notebooks, bridge indices, and generator-only artifacts are intentionally excluded from the iOS runtime tree.

Open `ChongzhenSimulator/ChongzhenSimulator.xcodeproj` in Xcode and run the `ChongzhenSimulator` scheme on an iPhone simulator.

## LLM API configuration

The app has two separate LLM routes:

- Text generation: `CHONGZHEN_TEXT_API_BASE_URL`, `CHONGZHEN_TEXT_API_KEY`, `CHONGZHEN_TEXT_API_MODEL`
- Image generation: `CHONGZHEN_IMAGE_API_BASE_URL`, `CHONGZHEN_IMAGE_API_KEY`, `CHONGZHEN_IMAGE_API_MODEL`

For simulator-only development, pass these as scheme environment variables in Xcode. Do not commit real API keys. For production, route these calls through a backend so keys are not shipped inside the app bundle.
