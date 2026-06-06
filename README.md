# Ming iOS Tiangang Lab

This orphan branch starts the iOS prototype from a blank tree.

The first prototype is deliberately small:

- SwiftUI portrait-only Tiangang viewer.
- Six official pieces: Han Kuang, Bi Ziyan, Cui Chengxiu, Cao Huachun, Yuan Chonghuan, and Zu Dashou.
- Full 36-dimension Tiangang values are visible in the development build.
- No backend, no LLM, no Python/Web runtime dependency.

The old Ming prototype is only used as a static content source. Run the import tool when the source Tiangang data changes:

```bash
cd TiangangLab
python3 Tools/import_tiangang_seed.py \
  --source /Users/zhujianzheng/Desktop/Ming/content/npc_tiangang.json \
  --output TiangangLab/Resources/tiangang_seed.json
```

Open `TiangangLab/TiangangLab.xcodeproj` in Xcode and run the `TiangangLab` scheme on an iPhone simulator.
