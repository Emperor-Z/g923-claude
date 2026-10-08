# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

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
