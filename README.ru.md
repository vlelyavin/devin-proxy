# devin-proxy

[README in english](README.md)

у тебя есть подписка devin / windsurf. инструментам нужен openai-совместимый эндпоинт. этот релей стоит посередине: превращает `/v1/chat/completions` в ConnectRPC-вызов, который понимает бэкенд подписки, чтобы hermes / opencode / curl ехали на подписке, а не на оплате за токены.

## что делает

- читает креды devin с диска на каждый запрос — ротация токена не требует рестарта
- транслирует openai chat completions (стриминг и нестриминг, tools, многошаговые `tool_calls`) в `exa.api_server_pb.ApiServerService/GetChatMessage`
- сохраняет thinking-подписи вызовов инструментов (gemini 4 argon, claude opus, swe-2), иначе stateless-клиент ломается на втором шаге tool-цикла
- переводит счётчики usage обратно в openai `usage`
- без зависимостей. только стандартная библиотека python.

## чем не является

- не продукт cognition / devin / windsurf, никак с ними не связан
- не хостед-сервис — работает на твоей машине, на твоих кредах
- не хранилище кредов — ничего приватного в репозитории нет

## честно

используются креды подписки вне официального клиента. это серая зона их tos. будешь молотить — можешь потерять подписку. решать тебе.

## статус

2026-10: подписка, под которую писался репозиторий, закончилась. `/v1/models` ещё отвечает из кэша каталога, реальные вызовы возвращают `502 permission_denied`, пока не подключена действующая подписка. код публикуется как есть.

## требования

- python 3.11+ (нужен `tomllib`)
- `~/.local/share/devin/credentials.toml` с `windsurf_api_key` (и опциональным `api_server_url`, по умолчанию `https://server.codeium.com`)

## запуск

```bash
git clone https://github.com/vlelyavin/devin-proxy.git
cd devin-proxy
python3 devin-proxy.py
```

слушает `127.0.0.1:18805`.

```bash
curl -sS http://127.0.0.1:18805/v1/models
```

## переменные окружения

| переменная | по умолчанию | смысл |
|---|---|---|
| `DEVIN_PROXY_HOST` | `127.0.0.1` | адрес прослушивания |
| `DEVIN_PROXY_PORT` | `18805` | порт |

## модели

те, что отдаёт твой план; с этими релей использовался:

```
gemini-4-argon-high / -medium / -low
claude-opus-5-5-high / -max
claude-fable-5-1-high
swe-2-high / -medium / -max
gpt-6-astra-high
gpt-6-sol-high
```

идентификаторы — полные. короткие алиасы бэкенд не разворачивает.

## systemd (опционально)

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

## лицензия

MIT. см. [LICENSE](LICENSE).
