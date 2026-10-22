# Perf notes

One line per experiment: date, game, build/commit, setting changed, result.

## Fallout: New Vegas

- Title ID: 425307E0 (disc 1 ISO, media ID 6470401C)
- Test spot: Goodsprings, outside Doc Mitchell's house
- Global config on iOS is `Documents/xenia-edge.config.toml` (not
  `xenios.config.toml`); per-game overrides in `Documents/config/425307E0.config.toml`.

| Date | Build | Change | Result |
|---|---|---|---|
| 2026-10-22 | b1f593125 (Canary port), iOS 26.3 | defaults (`protect_zero = true`) | Boot is intermittent: some launches stay on loading screens; two crashed with EXC_BAD_ACCESS at host 0x300000004 on the guest main thread (likely guest address 0x4, null pointer + 4, blocked by protect_zero) |
| 2026-10-22 | b1f593125 | `[Memory] protect_zero = false`, `[Logging] log_level = 2` | First launch reached the main menu (title music) in ~40 s; more launches needed to confirm |
| 2026-10-22 | b1f593125, 720p, 1x scale, framerate_limit 0 | `[Metal] metal_backend_telemetry = true`, interval 60 (measurement only) | Guest FPS (from telemetry, 30 s windows, ~±5%): ~30-33 at ~215 draws/frame (full speed; NV caps at 30), ~24-27 at ~1100-1800 draws/frame |
