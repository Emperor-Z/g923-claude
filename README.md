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

Your draft prompt is stashed (`Ctrl+S`) while the command is sent and restored after, so shifting mid-sentence doesn't lose it. `/model` applies immediately, even while Claude is working: the switch takes effect from Claude's next request. If Claude Code shows a prompt-cache warning, confirm it with the gas pedal.

**+ / −** step reasoning effort through `low → medium → high → xhigh → max` with `/effort`. The daemon assumes you start at `high` (configurable as `[effort] start`).

Gear presets live in `[shifter]` in `config.toml`. Each gear is a list of slash commands, so a gear can set several things at once, for example `gear_6 = ["/model fable", "/effort max"]`.

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

Letters are ordered by frequency (`e t a o i n s r h l …`). The dial keeps its position between characters, so the first press after a commit re-shows the same letter: double letters are one press plus Enter.

**What you see is what you get.** A previewed character or suggested word that's visible in the prompt stays there unless you press ○ to remove it, so ✕, Gas or moving on all keep it.

**Word suggestions.** After two committed letters, a notification shows the top three guesses. □ types the first one inline, and pressing □ again swaps it for the next; △ accepts it and adds a space (if nothing is showing, △ inserts the top guess directly). Guesses come from a built-in list (`g923/words.txt`) blended with words from your own past Claude Code prompts (`~/.claude/history.jsonl`), which are read locally at startup and never written anywhere.

Tips:
- `/` (top of the symbols set) opens Claude Code's command menu and `@` opens the file picker; drive those menus with the paddles and Gas.
- Paddles, pedals, gears and the other buttons keep their Drive mode actions while you type.
- For long prompts, hold L3 for voice dictation instead.

## Install

Requires Linux, Python 3.11+, and write access to `/dev/uinput` (granted to the logged-in user on most systemd distros).

```bash
git clone https://github.com/Emperor-Z/g923-claude.git
cd g923-claude
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# or: python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Usage

```bash
bin/g923d              # run the daemon, then press PS to arm
bin/g923d --dry-run    # print what each input would send, without sending it
bin/g923d --dump       # print raw wheel events (find button codes)
bin/g923d --config my.toml
```

Focus the terminal running Claude Code, press **PS**, and drive.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

The tests replay synthetic wheel events through the daemon, so they don't need the wheel connected.

## Safety

Keys go to whichever window is focused. The daemon starts **disarmed** — press **PS** to arm it (you get a notification). Disarm before gaming or using other apps.

## Roadmap

- [x] Phase 0 — skeleton, config, README
- [x] Phase 1 — daemon core, Drive mode, steering scroll, arm/disarm
- [x] Phase 2 — clutch + shifter model switching, effort
- [x] Phase 3 — Type mode with word suggestions
- [ ] Phase 4 — Claude Code hooks, feedback, systemd service, installer
- [ ] Phase 5 — force feedback via the new-lg4ff driver

## Hardware notes

- Tested on the G923 **PlayStation/PC** version (`046d:c266`). The Xbox version (`046d:c26e`) uses different button codes; adjust `[buttons]` in `config.toml`.
- Pedal and button codes are in `config.toml`. Run `g923d --dump` to see what your wheel reports.

## License

MIT
