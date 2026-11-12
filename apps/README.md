# App-specific assets

`configs/<id>.yml` is the small, declarative **manifest** (upstream repo,
container, artifact name, `workflow:`). Anything that only makes sense for one
application lives here:

```
apps/<id>/
├── README.md              # what was patched/vendored and why
├── patches/*.patch        # upstream-version-specific patches
├── third_party/**         # vendored headers or build inputs
└── scripts/**             # optional app-specific build helpers
```

Rules:

1. Keep the manifest free of build logic. Commands and compiler flags belong in
   the app's workflow or in `apps/<id>/scripts/`.
2. Name patches with the upstream version they target, e.g.
   `electrobun-2.0.5-webkit40.patch`. When upstream bumps the version, the patch
   must be reviewed rather than silently re-applied.
3. Do not add new files to top-level `patches/` or `third_party/`. They were
   global dumping grounds; new work goes under `apps/<id>/`. Shared, genuinely
   cross-app inputs need an explicit `shared/` directory and a review.
4. GitHub only executes workflows from `.github/workflows/`, so the entry point
   stays there. `configs/<id>.yml` names it with `workflow:` so the central
   watcher can dispatch the right one.
5. A dedicated workflow may hardcode its app's build path, but it must read the
   version/artifact facts from `configs/<id>.yml` where practical. If it cannot,
   document the duplication in the app README.

## Current apps

| id | build_type | workflow | assets |
|----|------------|----------|--------|
| dbx | tauri-appimage | `app-build.yml` | none |
| gonavi | go-wails | `gonavi-build.yml` | none |
| thinkrail | prebuilt-wrapper | `thinkrail-compat.yml` | `patches/`, `third_party/` |
