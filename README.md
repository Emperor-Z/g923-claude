# g923-claude

Drive [Claude Code](https://code.claude.com) with a **Logitech G923** racing wheel, pedals and Driving Force shifter.

Brake to interrupt Claude, gas to send, shift gears to change model, steer to scroll — and the wheel pushes back when Claude finishes or needs your approval.

> Status: in development. See [Roadmap](#roadmap).

## How it works

```
 G923 ──evdev──▶ g923d (Python daemon, systemd user service)
                   │  maps inputs → virtual keyboard + mouse (uinput) ──▶ focused terminal
                   │
                   ◀── Unix socket ◀── Claude Code hooks (prompt submitted, stop, permission)
                   │
                   └─▶ feedback: force feedback on the wheel, or notification + sound
```

The daemon reads the wheel through evdev and types into whichever window has focus through a virtual uinput keyboard, so it works on any Wayland compositor (GNOME included) and X11. Claude Code hooks report back over a Unix socket so the wheel can react.

## Controls

```
            [L-paddle ↑]                         [R-paddle ↓]
   ┌──────────────────────────────────────────────────────────┐
   │    ▲                                           △ todos    │
   │  ◀ ✚ ▶  arrows                       □ always  ○ no/Esc  │
   │    ▼                                           ✕ yes/⏎    │
   │  L2: transcript                        R2: background    │
   │  L3: voice (hold)                      R3: autocomplete  │
   │        [Share: DRIVE]  [Options: TYPE]  + −  ◉dial  ⏎    │
   │                   (PS: ARM / DISARM)                     │
   └──────────────────────────────────────────────────────────┘
   steer left = scroll up · steer right = scroll down (speed ∝ angle)

   Pedals:   [CLUTCH: shift / mode]  [BRAKE: Esc]  [GAS: Enter]
   Shifter:  1 Haiku · 2 Sonnet · 3 Opus · 4 Fable · 5 Opus 1M · 6 opusplan · R rewind
```

### Drive mode (default)

| Control | Sends | In Claude Code |
|---|---|---|
| Gas | `Enter` | Submit / accept highlighted option |
| Brake | `Esc` | Interrupt Claude; decline a permission prompt |
| Clutch (tap) | `Shift+Tab` | Cycle permission mode |
| Clutch + shifter | `/model …` | Change model (see below) |
| Paddles, D-pad ▲▼, dial | `↑` / `↓` | Move through menus and history |
| D-pad ◀▶ | `←` / `→` | Cursor, dialog tabs |
| Dial Enter, ✕ | `Enter` | Submit / Yes |
| ○ | `Esc` | No / close dialog |
| □ | `Shift+Tab` | "Allow for this session" on prompts, else cycle mode |
| △ | `Ctrl+T` | Toggle task checklist |
| L2 | `Ctrl+O` | Transcript viewer |
| R2 | `Ctrl+B` | Background the running command |
| R3 | `Tab` | Accept autocomplete |
| L3 (hold) | `Space` (held) | Voice dictation, if enabled in Claude Code |
| + / − | `/effort …` | Raise / lower reasoning effort |
| Steering | mouse wheel | Scroll the output |
| PS | — | Arm / disarm the whole thing |
| Share / Options | — | Switch to Drive / Type mode |

### Shifter = model

Hold the **clutch** and move the stick, like a real car. Shifting without the clutch is ignored (and grinds), so knocking the stick never changes your model.

| Gear | Model |
|---|---|
| 1 | `haiku` |
| 2 | `sonnet` |
| 3 | `opus` |
| 4 | `fable` |
| 5 | `opus[1m]` (1M context) |
| 6 | `opusplan` |
| R | Rewind (`Esc Esc`) |
| N | no change |

Your draft prompt is stashed (`Ctrl+S`) while the command is sent and restored after, so shifting mid-sentence doesn't lose it.

### Type mode

Press **Options** to type with the wheel; **Share** returns to Drive mode. The character you're choosing is typed straight into the prompt as a live preview and replaced as you scroll.

| Control | Action |
|---|---|
| D-pad ◀▶ | Previous / next character |
| Dial ↻↺ | Same, fast |
| D-pad ▲▼ | Switch set: `abc` → `ABC` → `123` → symbols |
| Dial Enter | Commit the character |
| ✕ | Space |
| ○ | Backspace |
| □ | Next suggested word |
| △ | Accept suggested word |
| Gas / Brake | Still Enter / Esc |

Letters are ordered by frequency (`e t a o i n s r h l …`). Word suggestions come from a built-in list plus your own past Claude Code prompts, read locally and never stored.

## Safety

Keys go to whichever window is focused. The daemon starts **disarmed** — press **PS** to arm it (you get a notification). Disarm before gaming or using other apps.

## Roadmap

- [x] Phase 0 — skeleton, config, README
- [ ] Phase 1 — daemon core, Drive mode, steering scroll, arm/disarm
- [ ] Phase 2 — clutch + shifter model switching, effort
- [ ] Phase 3 — Type mode with word suggestions
- [ ] Phase 4 — Claude Code hooks, feedback, systemd service, installer
- [ ] Phase 5 — force feedback via the new-lg4ff driver

## Hardware notes

- Tested on the G923 **PlayStation/PC** version (`046d:c266`). The Xbox version (`046d:c26e`) uses different button codes; adjust `[buttons]` in `config.toml`.
- Pedal and button codes are in `config.toml`. Run `g923d --dump` to see what your wheel reports.

## License

MIT
