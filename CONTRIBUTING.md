# Contributing

Thanks for wanting to help! This is a small project, so the process is light.

## Quick setup

```bash
git clone https://github.com/Emperor-Z/g923-claude.git
cd g923-claude
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .
```

The tests replay synthetic wheel events, so you don't need a wheel to work on the logic.

## Adding support for your wheel

This is the most useful contribution.

1. Run `bin/g923d --dump` and press every button, pedal and shifter position.
2. Create `configs/<vendor>-<model>.toml` with `[device] name_match` and only the `[pedals]` / `[buttons]` entries that differ from `config.toml` (configs are layered over the defaults).
3. Try it: `bin/g923d --config configs/<vendor>-<model>.toml --dry-run`.
4. Open a PR with the config and the `--dump` output, and add your wheel to the tables in `README.md` and `configs/README.md`. `tests/test_config.py` checks every shipped config.

## Code changes

- Keep changes focused: one logical change per commit.
- Add or update a test in `tests/` for behaviour changes.
- Run the test suite before opening a PR. CI runs it on Python 3.11 to 3.13.
- Every PR into `main` needs CI to pass.

## Ideas we'd love help with

- Force feedback effects (see the roadmap)
- Rev LEDs showing Claude's context usage
- macOS / Windows input backends
- Configs for G29, G920, Thrustmaster, Fanatec, Moza

## Code of conduct

Be kind. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
