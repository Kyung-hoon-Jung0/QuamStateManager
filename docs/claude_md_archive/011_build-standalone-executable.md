<!-- Moved verbatim from root CLAUDE.md (section: Commands, block 11) on 2026-11-14. -->

**Build standalone executable:**
```bash
pyinstaller build/quam-manager.spec
# Output: dist/quam-manager/quam-manager.exe
```
Uses **onedir mode** (not onefile) for instant cold start — onefile extracts to temp on every launch (3–10s overhead).
