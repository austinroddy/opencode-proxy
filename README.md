# opencode-proxy

`opencode-proxy` is a translation server that lets opencode use an
Ollama-shaped LLM endpoint through opencode's existing OpenAI-compatible
provider support.

The goal is to avoid patching opencode.

```text
opencode
  sends OpenAI-compatible requests
  to /v1/models and /v1/chat/completions

opencode-proxy
  translates those requests

your upstream endpoint
  receives Ollama-shaped requests
  at /api/tags and /api/chat
```

## What This Tool Does

opencode can use custom providers with:

```jsonc
"npm": "@ai-sdk/openai-compatible"
```

That means opencode expects an OpenAI-ish HTTP API:

- `GET /v1/models`
- `POST /v1/chat/completions`
- streaming responses as Server-Sent Events

Some local or company endpoints expose an Ollama-ish API instead:

- `GET /api/tags`
- `POST /api/chat`
- streaming responses as newline-delimited JSON

`opencode-proxy` bridges that mismatch.

It does not implement the OpenAI Responses API, embeddings, image generation, or
model management. It is intentionally small: one config file, one FastAPI app,
and tests that document the protocol conversion.

## First Files To Read

- `opp.toml`: local runtime config
- `opp.example.toml`: commented config template
- `opencode_proxy/app.py`: request/response translation
- `opencode_proxy/config.py`: TOML schema
- `opencode-proxy.sh`: start/stop/status helper
- `tests/test_app.py`: executable examples

## API Translation

| opencode calls proxy | proxy calls upstream | Notes |
| --- | --- | --- |
| `GET /v1/models` | `GET /api/tags` | Converts Ollama model list to OpenAI model list |
| `POST /v1/chat/completions` | `POST /api/chat` | Converts OpenAI chat body to Ollama chat body |
| OpenAI SSE stream | Ollama JSON-line stream | Emits `data: ...` chunks and final `data: [DONE]` |

OpenAI request fields mapped to Ollama:

| OpenAI field | Ollama field |
| --- | --- |
| `model` | `model` |
| `messages[].role` | `messages[].role` |
| string `messages[].content` | `messages[].content` |
| text parts in `messages[].content[]` | joined into `messages[].content` |
| image URL parts | base64 payloads in `messages[].images` |
| `tools` | `tools` |
| `temperature` | `options.temperature` |
| `top_p` | `options.top_p` |
| `max_tokens` / `max_completion_tokens` | `options.num_predict` |
| `stop` | `options.stop` |

Ollama response fields mapped to OpenAI:

| Ollama field | OpenAI field |
| --- | --- |
| `message.role` | `choices[0].message.role` or stream `delta.role` |
| `message.content` | `choices[0].message.content` or stream `delta.content` |
| `message.tool_calls` | `tool_calls` |
| `done_reason` | `finish_reason` |
| `prompt_eval_count` | `usage.prompt_tokens` |
| `eval_count` | `usage.completion_tokens` |

## Configuration

Runtime configuration lives in `opp.toml`.

Start from the example:

```bash
cp opp.example.toml opp.toml
```

Local Ollama:

```toml
[server]
host = "127.0.0.1"
port = 11435

[upstream]
base_url = "http://localhost:11434"
request_timeout_seconds = 300

[auth]
client_api_keys = []
```

Company upstream with bearer auth:

```toml
[server]
host = "127.0.0.1"
port = 11435

[upstream]
base_url = "https://llm-internal.example.com"
api_key = "upstream-token"
request_timeout_seconds = 300

[auth]
client_api_keys = []
```

Shared server with client auth:

```toml
[server]
host = "0.0.0.0"
port = 11435

[upstream]
base_url = "http://ollama.internal:11434"
api_key = "upstream-token-if-needed"
request_timeout_seconds = 300

[auth]
client_api_keys = ["team-opencode-token"]
```

When `auth.client_api_keys` is non-empty, callers must send either:

```text
Authorization: Bearer team-opencode-token
```

or:

```text
x-api-key: team-opencode-token
```

The opencode custom provider sends `options.apiKey` as a bearer token through
the OpenAI-compatible SDK.

## Local Install

```bash
cd /Users/austin/Documents/GitHub/opencode-proxy
python -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
```

## Start And Stop Locally

Use the shell helper from the repo root:

```bash
./opencode-proxy.sh start
./opencode-proxy.sh status
./opencode-proxy.sh stop
```

Verbose mode:

```bash
./opencode-proxy.sh start --verbose
./opencode-proxy.sh stop --verbose
```

Explicit config:

```bash
./opencode-proxy.sh start --config /path/to/opp.toml
```

The script:

- creates `.venv` if missing
- installs or updates the package in `.venv`
- starts the proxy in the background
- writes `.opencode-proxy.pid`
- writes logs to `.opencode-proxy.log`

Direct CLI:

```bash
opencode-proxy --config ./opp.toml --verbose
```

Health check:

```bash
curl http://127.0.0.1:11435/health
```

Model check:

```bash
curl http://127.0.0.1:11435/v1/models
```

Chat check:

```bash
curl http://127.0.0.1:11435/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{
    "model": "qwen2.5-coder:32b",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": false
  }'
```

## Quick Setup With opencode

Use this flow when each developer runs their own local proxy.

1. Create the proxy config:

```bash
cd /Users/austin/Documents/GitHub/opencode-proxy
cp opp.example.toml opp.toml
```

2. Edit `opp.toml` so `[upstream].base_url` points at your Ollama-shaped
   endpoint:

```toml
[server]
host = "127.0.0.1"
port = 11435

[upstream]
base_url = "http://localhost:11434"
request_timeout_seconds = 300

[auth]
client_api_keys = []
```

For a company endpoint that needs auth:

```toml
[upstream]
base_url = "https://llm-internal.example.com"
api_key = "upstream-token"
request_timeout_seconds = 300
```

3. Start the proxy:

```bash
./opencode-proxy.sh start --verbose
```

4. Verify the proxy is reachable:

```bash
curl http://127.0.0.1:11435/health
curl http://127.0.0.1:11435/v1/models
```

5. Add a custom provider to opencode.

Use either your project config:

```text
./opencode.json
```

or your global user config:

```text
~/.config/opencode/opencode.json
```

Example `opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "company-ollama": {
      "name": "Company Ollama",
      "npm": "@ai-sdk/openai-compatible",
      "api": "http://127.0.0.1:11435/v1",
      "models": {
        "qwen2.5-coder:32b": {
          "name": "Qwen 2.5 Coder 32B",
          "tool_call": true,
          "temperature": true,
          "limit": {
            "context": 32768,
            "output": 8192
          }
        }
      },
      "options": {
        "apiKey": "local-proxy"
      }
    }
  },
  "model": "company-ollama/qwen2.5-coder:32b"
}
```

6. Start opencode normally from the project using that config:

```bash
opencode
```

Or explicitly select the model:

```bash
opencode --model company-ollama/qwen2.5-coder:32b
```

Important values:

- `provider.company-ollama.api` must end in `/v1`; the proxy owns `/v1/models`
  and `/v1/chat/completions`.
- `provider.company-ollama.npm` must be `@ai-sdk/openai-compatible`.
- `provider.company-ollama.models` must list the model IDs your upstream
  accepts.
- `model` uses the opencode format `provider-id/model-id`.
- `options.apiKey` can be any non-empty value for an unauthenticated local
  proxy. For a shared proxy with `[auth].client_api_keys`, it must match one of
  those configured keys.

## opencode Client Config

Add a custom OpenAI-compatible provider to opencode.

Local proxy example:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "company-ollama": {
      "name": "Company Ollama",
      "npm": "@ai-sdk/openai-compatible",
      "api": "http://127.0.0.1:11435/v1",
      "models": {
        "qwen2.5-coder:32b": {
          "name": "Qwen 2.5 Coder 32B",
          "tool_call": true,
          "temperature": true,
          "limit": {
            "context": 32768,
            "output": 8192
          }
        }
      },
      "options": {
        "apiKey": "local-proxy"
      }
    }
  },
  "model": "company-ollama/qwen2.5-coder:32b"
}
```

Shared server example:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "company-ollama": {
      "name": "Company Ollama",
      "npm": "@ai-sdk/openai-compatible",
      "api": "https://opencode-proxy.example.com/v1",
      "models": {
        "qwen2.5-coder:32b": {
          "name": "Qwen 2.5 Coder 32B",
          "tool_call": true,
          "temperature": true,
          "limit": {
            "context": 32768,
            "output": 8192
          }
        }
      },
      "options": {
        "apiKey": "team-opencode-token"
      }
    }
  },
  "model": "company-ollama/qwen2.5-coder:32b"
}
```

The model IDs in opencode must match the model IDs your upstream expects.

## Server-Side Integration

Use this mode when you want one central proxy and many opencode users. Users
only configure opencode to call your proxy URL; they do not run this repo.

Recommended layout:

```text
developer laptops
  opencode -> https://opencode-proxy.example.com/v1

company server
  reverse proxy / TLS / SSO or network allowlist
  -> opencode-proxy on 127.0.0.1:11435
  -> internal Ollama-shaped endpoint
```

Server `opp.toml`:

```toml
[server]
host = "127.0.0.1"
port = 11435

[upstream]
base_url = "http://ollama.internal:11434"
request_timeout_seconds = 300

[auth]
client_api_keys = ["team-opencode-token"]
```

Install on the server:

```bash
git clone <this-repo-url> /opt/opencode-proxy
cd /opt/opencode-proxy
cp opp.example.toml opp.toml
python -m venv .venv
source .venv/bin/activate
pip install "."
```

Run manually:

```bash
./opencode-proxy.sh start --verbose
```

Systemd example:

```ini
[Unit]
Description=opencode-proxy
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/opencode-proxy
ExecStart=/opt/opencode-proxy/.venv/bin/opencode-proxy --config /opt/opencode-proxy/opp.toml
Restart=always
RestartSec=5
User=opencode-proxy

[Install]
WantedBy=multi-user.target
```

Nginx example:

```nginx
server {
  listen 443 ssl;
  server_name opencode-proxy.example.com;

  location / {
    proxy_pass http://127.0.0.1:11435;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 360s;
  }
}
```

For production, put TLS and access control in front of the proxy. You can use
`auth.client_api_keys` for a simple bearer-token gate, and also restrict network
access through your ingress, VPN, SSO proxy, or firewall.

After server deployment, each user only needs the shared opencode config block
with:

```jsonc
"api": "https://opencode-proxy.example.com/v1",
"options": { "apiKey": "team-opencode-token" }
```

## Development

Run tests:

```bash
.venv/bin/python -m pytest -q
```

Run Ruff:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format .
```

Run nox:

```bash
.venv/bin/python -m nox
```
