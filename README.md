# devin-proxy

[README на русском](README.ru.md)

you have a devin / windsurf subscription. your tools want an openai-compatible endpoint. this sits in the middle: a local relay that turns `/v1/chat/completions` into the ConnectRPC call the subscription backend speaks, so hermes / opencode / curl ride on the sub instead of a per-token bill.

## what it does

- reads your devin credentials from disk on every request — token rotation needs no restart
- translates openai chat completions (streaming and non-streaming, tools, multi-turn `tool_calls`) into `exa.api_server_pb.ApiServerService/GetChatMessage`
- keeps tool-call thinking signatures (gemini 4 argon, claude opus, swe-2) so stateless openai clients don't break on the second turn of a tool loop
- maps usage counters back into openai `usage`
- zero dependencies. python stdlib only.

## what it is not

- not a cognition / devin / windsurf product, not affiliated with them
- not a hosted service — runs on your machine, your creds
- not a cred bundle — nothing private ships in this repo

## straight talk

it uses subscription credentials outside the official client. that's a gray zone in their tos. hammer it and you can lose the subscription. your call.

## status

2026-10: the subscription this repo was built against has lapsed. `/v1/models` still answers from the cached catalog, real calls return `502 permission_denied` until a valid subscription is attached again. the code is published as-is.

## requirements

- python 3.11+ (uses `tomllib`)
- `~/.local/share/devin/credentials.toml` with `windsurf_api_key` (and optional `api_server_url`, default `https://server.codeium.com`)

## run

```bash
git clone https://github.com/vlelyavin/devin-proxy.git
cd devin-proxy
python3 devin-proxy.py
```

listens on `127.0.0.1:18805`.

```bash
curl -sS http://127.0.0.1:18805/v1/models
```

## env

| var | default | meaning |
|---|---|---|
| `DEVIN_PROXY_HOST` | `127.0.0.1` | bind address |
| `DEVIN_PROXY_PORT` | `18805` | bind port |

## models

whatever your plan exposes; the ones this was used with:

```
gemini-4-argon-high / -medium / -low
claude-opus-5-5-high / -max
claude-fable-5-1-high
swe-2-high / -medium / -max
gpt-6-astra-high
gpt-6-sol-high
```

use full ids. short aliases are not resolved by the backend.

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

## license

MIT. see [LICENSE](LICENSE).
