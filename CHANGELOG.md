# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added
- Type mode: holding ○ repeats backspace like a keyboard key, then switches to deleting whole words (`Ctrl+W`); timings configurable in `[type]`

## [0.5.1] - 2026-10-08

### Added
- `configs/` for other wheels, starting with an (untested) Logitech G29 config
- Published to PyPI: `pipx install g923-claude`

### Changed
- Config files are layered over the defaults, so user and wheel configs only list what they change

## [0.5.0] - 2026-10-08

### Added
- Force feedback on the wheel (new-lg4ff for the PS G923): kicks when Claude finishes, wiggle while a permission prompt waits, buzz on a clutchless shift, centring spring; `g923d --test-ffb`

### Changed
- Type mode letters now run a to z instead of frequency order, which is easier to remember

### Fixed
- `bin/g923d` failed to start under systemd (`No module named 'g923'`)

## [0.4.0] - 2026-10-08

First public release.

### Added
- Drive mode: pedals (gas = Enter, brake = Esc, clutch tap = Shift+Tab), paddles, D-pad, dial and face buttons mapped to Claude Code shortcuts
- Steering scrolls output, with speed proportional to angle
- PS button arms and disarms; starts disarmed
- Clutch + H-shifter switches model (`/model`); shifting without the clutch is ignored; your draft prompt is preserved
- +/− step reasoning effort (`/effort`)
- Type mode: rotary character picker with live preview and word suggestions learned from local prompt history
- Claude Code hooks: chime when Claude stops, repeated alert while a permission prompt waits
- `install.sh` / `uninstall.sh`, systemd user service, pip-installable package, release wheels and GHCR image
