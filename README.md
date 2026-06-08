# 崇祯模拟器 iOS

This orphan branch starts the iOS prototype from a blank tree.

The first prototype is deliberately small:

- SwiftUI portrait-only Tiangang viewer.
- Six official pieces: Han Kuang, Bi Ziyan, Cui Chengxiu, Cao Huachun, Yuan Chonghuan, and Zu Dashou.
- Swipeable official portraits are fixed at the top of the iOS UI.
- Full 36-dimension Tiangang values are visible in the development build, with value stances shown separately from professional skill bars.
- Tiangang dimension labels include player-facing explanations for every stance and skill level.
- The current viewer works without backend, LLM, or Python/Web runtime dependencies; optional LLM service routes are scaffolded for future gameplay features.
- The game UI uses the bundled Simplified Chinese calligraphy-style font `LXGWWenKai-Medium.ttf` through `MingTypography`.

This branch now keeps clean seed data for the iOS prototype. These seeds are not yet a complete simulation foundation; P0 now focuses on connecting them into a gameplay-ready Foundation Graph.

- `ChongzhenSimulator/Resources/NPCDatabase` contains NPC seed data.
- `ChongzhenSimulator/Resources/EnvironmentDatabase` contains administrative, institution, office, rank, location, and eunuch-agency seed data.
- `npc_foundation_base_info_seed.json` is the first new NPC foundation table; older NPC seed tables are marked as legacy import assets.
- Build notebooks, bridge indices, and generator-only artifacts are intentionally excluded from the iOS runtime tree.

P0 data audits:

- `python3 ChongzhenSimulator/Tools/audit_npc_database.py`
- `python3 ChongzhenSimulator/Tools/audit_environment_database.py`
- `python3 ChongzhenSimulator/Tools/audit_foundation_graph.py`

See `Docs/GameDesign.md` for the current game design, administrative model, official-rank simplifications, and data-foundation rules.

See `Docs/IOSNativeImplementationPlan.md` for the native iOS technical stack, module breakdown, quantified milestones, and v1 execution plan.

Open `ChongzhenSimulator/ChongzhenSimulator.xcodeproj` in Xcode and run the `ChongzhenSimulator` scheme on an iPhone simulator.

## LLM API configuration

The project has two shared LLM routes for both development support and in-game runtime features:

- Text generation: supports script/literary writing and numeric design during development, and in-game dialogue, memorials, event narration, and decision generation.
- Image generation: supports illustration, icon, and art-asset creation during development, and in-game illustration, portrait, and visual-feedback generation.

Environment variables:

- Text generation: `CHONGZHEN_TEXT_API_BASE_URL`, `CHONGZHEN_TEXT_API_KEY`, `CHONGZHEN_TEXT_API_MODEL`
- Image generation: `CHONGZHEN_IMAGE_API_BASE_URL`, `CHONGZHEN_IMAGE_API_KEY`, `CHONGZHEN_IMAGE_API_MODEL`, `CHONGZHEN_IMAGE_API_IMAGE_SIZE`

Current defaults are DeepSeek `deepseek-v4-flash` for text and Google `gemini-3.1-flash-image` at `1K` image size for image generation. For simulator-only development, pass API keys as scheme environment variables in Xcode. Do not commit real API keys. For production, route these calls through a backend so keys are not shipped inside the app bundle.
