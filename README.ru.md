# devin-proxy

[README in english](README.md)

У вас есть подписка на Devin / Windsurf, а сторонним инструментам требуется OpenAI-совместимый API. Этот релей выступает мостом: принимает запросы к `/v1/chat/completions` и транслирует их в вызовы ConnectRPC, понятные бэкенду подписки. Это позволяет запускать hermes, opencode или curl за счёт подписки, не оплачивая токены отдельно.

## Что делает

- Читает учетные данные Devin с диска при каждом запросе: ротация токена не требует перезапуска сервиса.
- Транслирует запросы OpenAI Chat Completions (стриминг, обычный режим, tools и многошаговые `tool_calls`) в вызовы `exa.api_server_pb.ApiServerService/GetChatMessage`.
- Сохраняет thinking-подписи для вызовов инструментов (Gemini 4 Argon, Claude Opus, SWE-2), предотвращая сбои stateless-клиентов на втором шаге цикла инструментов.
- Конвертирует метрики usage бэкенда обратно в формат OpenAI `usage`.
- Работает без сторонних зависимостей - используется только стандартная библиотека Python.

## Чем не является

- Не является официальным продуктом Cognition, Devin или Windsurf и никак с ними не связан.
- Не является облачным сервисом: запускается локально и использует только ваши учетные данные.
- Не является хранилищем секретов: репозиторий не содержит приватных данных.

## Честно

Сервер использует учетные данные подписки в обход официального клиента. Это серая зона с точки зрения ToS платформы: чрезмерная нагрузка может привести к блокировке аккаунта. Используйте на свой страх и риск.

## Статус

2026-10: подписка, под которую писался репозиторий, закончилась. Эндпоинт `/v1/models` всё ещё отвечает из кэша каталога, однако реальные запросы завершаются с ошибкой `502 permission_denied`, пока не будет подключена действующая подписка. Код предоставляется «как есть».

## Требования

- Python 3.11+ (требуется встроенный `tomllib`)
- Файл `~/.local/share/devin/credentials.toml` с ключом `windsurf_api_key` (и опциональным `api_server_url`, по умолчанию `https://server.codeium.com`)

## Запуск

```bash
git clone https://github.com/vlelyavin/devin-proxy.git
cd devin-proxy
python3 devin-proxy.py
```

Сервер слушает на `127.0.0.1:18805`.

Проверка доступных моделей:

```bash
curl -sS http://127.0.0.1:18805/v1/models
```

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DEVIN_PROXY_HOST` | `127.0.0.1` | Адрес прослушивания |
| `DEVIN_PROXY_PORT` | `18805` | Порт |

## Модели

Доступны любые модели, входящие в ваш тарифный план. Релей проверялся со следующими:

```
gemini-4-argon-high / -medium / -low
claude-opus-5-5-high / -max
claude-fable-5-1-high
swe-2-high / -medium / -max
gpt-6-astra-high
gpt-6-sol-high
```

Необходимо указывать полные идентификаторы: короткие алиасы бэкенд не разрешает.

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

## Лицензия

MIT. См. [LICENSE](LICENSE).
