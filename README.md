# devin-proxy

[README на русском](README.ru.md)

If you have a Devin / Windsurf subscription and your tools require an OpenAI-compatible endpoint, this sits in the middle. It is a local relay that translates `/v1/chat/completions` into the ConnectRPC protocol used by the subscription backend, allowing tools like Hermes, OpenCode, or curl to run against your subscription instead of billing per token.

## What it does

- Reads Devin credentials from disk on every request, picking up rotated tokens without requiring a restart.
- Translates OpenAI chat completions (streaming, non-streaming, function calling, and multi-turn `tool_calls`) into `exa.api_server_pb.ApiServerService/GetChatMessage`.
- Preserves tool-call thinking signatures (Gemini 4 Argon, Claude Opus, SWE-2) so stateless OpenAI clients remain stable across multi-turn tool loops.
- Maps backend usage counters back into OpenAI-compatible `usage` payloads.
- Zero dependencies-relies solely on the Python standard library.

## What it is not

- Not an official Cognition / Devin / Windsurf product and not affiliated with them in any way.
- Not a hosted service-runs locally on your machine with your own credentials.
- Not a credential distributor-contains no private keys or tokens.

## Straight talk

This tool uses subscription credentials outside the official client, which sits in a ToS gray area. Excessive or abusive traffic may result in your subscription being terminated. Use it at your own risk.

## Status

2026-10: The subscription this project was built against has lapsed. `/v1/models` still responds from the cached catalog, but actual completion calls will return `502 permission_denied` until backed by an active subscription. The code is published as-is.

## Requirements

- Python 3.11+ (uses standard library `tomllib`)
- `~/.local/share/devin/credentials.toml` containing `windsurf_api_key` (and optional `api_server_url`, defaults to `https://server.codeium.com`)

## Run

```bash
git clone https://github.com/vlelyavin/devin-proxy.git
cd devin-proxy
python3 devin-proxy.py
```

Listens on `127.0.0.1:18805`.

```bash
curl -sS http://127.0.0.1:18805/v1/models
```

## Env

| var | default | meaning |
|---|---|---|
| `DEVIN_PROXY_HOST` | `127.0.0.1` | bind address |
| `DEVIN_PROXY_PORT` | `18805` | bind port |

## Models

Exposes whatever models your plan provides. Tested with:

```
gemini-4-argon-high / -medium / -low
claude-opus-5-5-high / -max
claude-fable-5-1-high
swe-2-high / -medium / -max
gpt-6-astra-high
gpt-6-sol-high
```

Always use full model IDs; the backend does not resolve short aliases.

## systemd (optional)

```ini
[Unit]
Description=devin-proxy - openai-compatible relay over the devin subscription
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/devin-proxy/devin-proxy.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

## License

MIT. See [LICENSE](LICENSE).
