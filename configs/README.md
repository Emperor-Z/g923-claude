# Wheel configs

Each file here is layered over the main [`config.toml`](../config.toml), so it only lists what differs for that device.

| File | Device | Status |
|---|---|---|
| (defaults) | Logitech G923 PlayStation/PC `046d:c266` | ✅ Tested |
| `logitech-g29.toml` | Logitech G29 `046d:c24f` | 🟡 Untested ([#11](https://github.com/Emperor-Z/g923-claude/issues/11)) |

```bash
bin/g923d --config configs/logitech-g29.toml
# or make it permanent:
mkdir -p ~/.config/g923-claude && cp configs/logitech-g29.toml ~/.config/g923-claude/config.toml
```

Adding a wheel: run `bin/g923d --dump`, press everything, and put only the codes that differ in a new `configs/<vendor>-<model>.toml`. See [CONTRIBUTING.md](../CONTRIBUTING.md#adding-support-for-your-wheel).
