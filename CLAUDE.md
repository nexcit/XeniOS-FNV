# XeniOS fork: Claude Code working notes

This is a personal copy of XeniOS (the iOS/macOS port of the Xenia Xbox 360
emulator, upstream: https://github.com/xenios-jp/XeniOS). The goal is to make games
run better on the user's iPhone by tuning config and changing emulator code, with
a fast feedback loop between a PC and the phone.

**Target game: Fallout: New Vegas (Xbox 360).** Repo: `nexcit/XeniOS-FNV` (not a
GitHub fork; `upstream` remote = xenios-jp/XeniOS). macOS CI builds are slow, so
batch experiments behind cvars and lean on Loop A first.

Machine- and person-specific notes live in `CLAUDE.local.md` (git-ignored, never
commit it). This repo may be public: never write personal details (names, emails,
device names/UDIDs, local paths, account info) into tracked files or commit messages.

## The setup you are working in

- You run on a **Windows PC**. There is no Mac. You cannot build or run the iOS
  app locally.
- iOS builds happen on **GitHub Actions** (`.github/workflows/ios-dev-build.yml`,
  macOS runner, ~30–60 min). Each successful build replaces the rolling pre-release
  `dev-latest`, whose IPA is always at
  `https://github.com/nexcit/XeniOS-FNV/releases/download/dev-latest/XeniOS-dev.ipa`.
- The user installs the IPA on the phone with **SideStore** and enables JIT with
  **StikDebug** before every play session (games won't start without JIT).
- The iPhone is plugged into this PC by **USB**. `tools/phone/phone.py` talks to it
  through `pymobiledevice3`. Everything it fetches goes to `.phone/` (git-ignored).
- You cannot see the phone's screen. The user reports what they see; you read logs.

## Phone helper: `python tools/phone/phone.py <command>`

| Command | What it does |
|---|---|
| `check` | Phone visible? XeniOS installed? Saves bundle ID to `.phone/app.json`. Run first. |
| `find` | Re-detect the app after a new build is installed. |
| `watch` | Stream the phone's system log (XeniOS process only) to `.phone/syslog-live.log`. Long-running: ask the user to run it in their own terminal, or start it in the background. `--all` = no filter (use for JIT/StikDebug or jetsam/memory-kill problems). |
| `tail-log` | Re-download `xenia.log` every 5 s to `.phone/device/xenia.log`. Long-running, same as `watch`. **This is the main live emulator log.** |
| `pull` | One-shot download of `xenia.log`, `xenios.config.toml`, `config/` into `.phone/device/`. |
| `push-config FILE [REMOTE]` | Upload a `.toml` config into the app's Documents. Backs up the phone's copy to `.phone/backups/<time>/` first. |
| `crashes` | Download XeniOS crash reports to `.phone/crashes/`. |
| `mark TEXT` | Put a `=== MARK ... ===` line in the live syslog so runs can be told apart. |

Where the useful information is:
- **`.phone/device/xenia.log`**: the emulator's own log (Xenia writes it to
  `Documents/xenia.log` on iOS). Its stdout does **not** reach the iOS syslog, so this
  file is the place to look for emulator messages.
- **`.phone/syslog-live.log`**: iOS-side messages (os_log, Metal/system errors, app
  being killed for memory, crashes). Use `--all` when hunting kills or JIT issues.
- **`.phone/crashes/*.ips`**: symbolicate mentally against the commit in `.phone/app.json`
  (`build` = git commit count of that build).

## Loop A: config tuning (no rebuild, minutes per try)

Do this first for any "make game X faster" request. Many wins are config-only.

1. `phone.py pull` to get the current configs.
2. Edit `.phone/device/xenios.config.toml` (global) or create/edit a per-game file
   `.phone/device/config/<TITLEID>.config.toml` (title ID is in `xenia.log` at boot).
   Per-game files override the global one. Keep per-game files small: only the keys
   being changed.
3. `phone.py push-config <file>`, then ask the user to restart the game, play the same
   test scene for ~1 minute, and tell you FPS/feel. Use `mark` before each run.
4. Compare logs between runs. Change **one thing at a time** and write down results
   in `perf-notes.md` (game, setting, result) so nothing is tested twice.

Verified option names (TOML section = the cvar's category in the source):

- `[Logging]` `log_level` (0=error 1=warning 2=info 3=debug; iOS default 1, use 2 while
  testing, 3 only for short repros since it is heavy), `flush_log` (default true),
  `log_mask` (bitmask to silence Kernel=1 Apu=2 Cpu=4 Gpu=8).
- `[Console]` `internal_display_resolution` (0–17, default 8 = 1280x720).
- `[GPU]` `readback_resolve` (`fast` default / `some` / `full` = accurate but slow /
  `none`), `readback_memexport`, `framerate_limit` (0 = unlimited),
  `guest_display_refresh_cap`, `async_shader_compilation`, `draw_resolution_scale_x/_y`,
  `clear_memory_page_state`.
- `[Metal]` `metal_backend_telemetry` (+ `metal_backend_telemetry_interval`): logs
  Metal backend counters every N guest swaps at info level (needs `log_level >= 2`).
  Also `metal_pipeline_creation_threads`, `metal_shader_disk_cache`,
  `metal_pipeline_binary_archive`, `metal_use_heaps`, `metal_texture_upload_via_blit`.
- `[iOS]` `ios_emulator_thread_user_initiated_qos`, `ios_guest_threads_user_initiated_qos`
  (scheduler experiments for when guest CPU work falls behind).

Before suggesting any other option, confirm it exists:
`git grep -n "DEFINE_[a-z0-9_]*(\s*<name>" -- src` (definitions can span lines; also
grep the name alone). Don't invent option names.

## Loop B: code changes (one CI build per try)

1. Branch: `dev/<short-topic>` from the current work branch.
2. Make the change. **Prefer gating every behavior change behind a new cvar**
   (`DEFINE_bool(my_feature, false, "...", "iOS")`). Then a single build can test
   on/off via `push-config` (Loop A) instead of needing two builds.
3. Format changed files: `clang-format -i <files>` using the repo's `.clang-format`
   (CI lint uses clang-format 21.1.8: `pip install clang-format==21.1.8`).
4. Commit with a clear message, push. Pushing a `dev/**` branch starts the build; or
   run `gh workflow run ios-dev-build.yml --ref <branch> -f note="<what to test>"`.
5. `gh run watch` (get the id from `gh run list --workflow ios-dev-build.yml -L 1`).
   On failure: `gh run view <id> --log-failed`, fix, push again. Don't make the user wait
   on a red build.
6. On success tell the user in one line: build is up, what to test, and that the link is
   the usual `dev-latest/XeniOS-dev.ipa`. They install it in SideStore, re-enable JIT
   in StikDebug, then you run `phone.py find` and continue with logs.

Because builds are slow, batch: look hard at the code and the logs before pushing,
and bundle related experiments behind separate cvars in one build.

Compile errors in `.mm` / Metal / iOS-only files only show up in CI. Shared C++
(`src/xenia/cpu`, `kernel`, `base`) can optionally be compile-checked on Windows with
`xb setup` + `xb build` (Visual Studio 2022/2026 required) if the user has set that up.

## Good first task: make performance measurable

There is no FPS/frame-time line in the log today. Before optimizing anything, add an
iOS perf logger behind a cvar (e.g. `[iOS] ios_perf_log`, default false): once per
second, log guest swaps/sec (game FPS), host present rate, and average/worst frame
time with `XELOGI("Perf: ...")`. The guest swap path is
`MetalCommandProcessor::IssueSwap` in `src/xenia/gpu/metal/metal_command_processor.cc`
(it already counts swaps for `MaybeDumpBackendTelemetry`); host presentation is in the
presenter under `src/xenia/ui/`. With that in `xenia.log`, every Loop A/B test has numbers.

## Fallout: New Vegas notes

- Get the title ID from `xenia.log` at boot and use it for the per-game config
  (`config/<TITLEID>.config.toml`). Record it here once known.
- Track FNV status in `perf-notes.md`: boots? menu? in-game FPS at a fixed test spot
  (e.g. Goodsprings, outside Doc Mitchell's house), crashes, memory kills (jetsam).
- Mods: only file-replacement mods for the **Xbox 360** data format are realistic.
  Tale of Two Wastelands is **not** feasible: it needs xNVSE + JIP LN (PC DLL plugins),
  its installer only takes PC FO3/FNV data, and the 360's 512 MB guest RAM is fixed.
  Don't start TTW work without the user explicitly asking again.

## Rules

- Never commit `.phone/`, game files, save data, title keys or anything from the phone.
- Don't touch JIT/W^X memory mapping, `xenia_ios.entitlements`, signing or the
  bundle ID logic without asking the user first; breaking those makes the app unusable.
- Don't push to `main`/`xenios` or open PRs upstream unless the user asks. `gh` must
  target the fork (`gh repo set-default nexcit/XeniOS-FNV`), never `xenios-jp/XeniOS`.
- The upstream `CI` and `Apple Publish` workflows are disabled in this fork on
  purpose; don't re-enable them.
- When syncing with upstream (`git fetch upstream && git merge upstream/xenios`),
  check that `tools/build_apple_release.sh` options used by `ios-dev-build.yml` still
  exist and that the upstream `apple-publish.yml` iOS job hasn't changed its setup
  steps (Xcode version, submodules); mirror any changes into `ios-dev-build.yml`.
- Keep `perf-notes.md` updated with what was tried and the measured result.
- Long-running commands (`watch`, `tail-log`, `gh run watch`) must run in the
  background or in the user's terminal, never block the session.
