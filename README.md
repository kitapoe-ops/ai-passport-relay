# ai-passport-relay

Muse-relay voice pipeline for the FoloToy AI Passport (ESP32-C3).

The device posts a 16 kHz mono WAV. The relay runs speech-to-text, streams a short LLM reply as caption frames, resamples text-to-speech from 24 kHz to 16 kHz, and closes the turn. Long jobs go to a Cursor automation webhook and come back through `/callback`.

Python 3.11+. The runtime is the standard library only.

## Frame stream

`POST /ask` responds `200` with `Content-Type: application/octet-stream` and HTTP chunked transfer. After the HTTP stack reassembles the body, the bytes are a sequence of muse frames:

| offset | size | meaning |
| --- | --- | --- |
| 0 | 1 | type: `0x00` END, `0x01` TEXT, `0x02` AUDIO |
| 1 | 4 | payload length, uint32 little-endian |
| 5 | N | payload |

- TEXT payload is a UTF-8 caption delta.
- AUDIO payload is PCM16LE, 16 kHz, mono, in chunks of 16 KiB (the last chunk is shorter).
- END has length 0. A turn is `TEXT*` then `AUDIO*` then one END.

HTTP chunks are only the transfer encoding. Parse muse frames from the dechunked byte stream; do not treat one HTTP chunk as one frame.

## Routes

| method | path | auth | behavior |
| --- | --- | --- | --- |
| POST | `/ask` | `Authorization: Bearer $RELAY_TOKEN` | WAV body, max 4 MiB, 16 kHz mono PCM16. Streams frames. |
| POST | `/task` | `Authorization: Bearer $RELAY_TOKEN` | JSON `{"input":"..."}`. Fires the Cursor webhook, stores the task, returns `202 {"task_id":"..."}`. |
| POST | `/callback` | `X-Callback-Secret: $CALLBACK_SECRET` | JSON `{"task_id","status":"done"\|"failed","summary":<=500,"artifact_url"?}`. `401` on a bad secret. |
| GET | `/task/{id}` | none | Latest task record, or `404`. |
| GET | `/health` | none | `{"ok":true,"tasks":N}` |
| GET | `/last` | none | Last `/ask` turn: heard text, reply, timings (`stt_ms`, `llm_ttfb_ms`, `llm_ms`, `tts_ms`, `total_ms`). |

`/health`, `/last`, and `GET /task/{id}` are open so a LAN operator can debug. Do not publish them on the public internet. `/last` contains the recognized speech.

Empty `RELAY_TOKEN` or `CALLBACK_SECRET` fails closed: those routes return `401`.

An upstream `503` whose body contains `token expired` becomes a TEXT frame `系統忙，等陣再試` and END (no audio). Other upstream failures become `暫時答唔到，等陣再試` and END. Once the WAV is accepted the device always gets a `200` frame stream.

A blank transcript skips the LLM and TTS and returns `聽唔清，請再講一次`. TTS text is capped at 3000 characters. The chat request sends `max_tokens: 200` and no tools. The LLM socket timeout is 20 seconds (time to first byte, and any later idle gap). Invalid WAV, a missing `Content-Length`, or a body over 4 MiB is rejected with HTTP 400/411/413 before any frame is sent.

## Environment

Secrets are read from the environment only. The process does not print them.

| variable | default | purpose |
| --- | --- | --- |
| `RELAY_HOST` | `0.0.0.0` | Bind address |
| `RELAY_PORT` | `8788` | Bind port |
| `RELAY_TOKEN` | empty (fail closed) | Bearer token for `/ask` and `/task` |
| `CALLBACK_SECRET` | empty (fail closed) | `X-Callback-Secret` for `/callback` |
| `TASK_STORE_PATH` | `tasks.jsonl` | Append-only task log. Latest line per `task_id` wins |
| `STT_URL` | `https://asrhkchat.kitahim.uk/api/stt` | Multipart field `audio`, JSON transcript. Timeout 30s |
| `LLM_BASE_URL` | `https://chat.kitahim.uk` | OpenAI-compatible base. Calls `$LLM_BASE_URL/v1/chat/completions` |
| `LLM_MODEL` | `hkgai-v3` | Chat model. Streaming, no tools |
| `TTS_URL` | `https://tts.kitahim.uk/api/tts` | JSON `{"text":"..."}` (1–3000 chars) → PCM16LE mono 24 kHz. Timeout 45s |
| `CURSOR_AUTOMATION_URL` | empty | `POST {"input":"<brief>"}` |
| `CURSOR_AUTOMATION_TOKEN` | empty | Bearer token for that webhook only |

The relay resamples TTS audio from 24 kHz to 16 kHz before framing it. It does not forward `RELAY_TOKEN` upstream.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'   # pytest; the server itself has no dependencies
python -m relay
```

Put secrets in a root-only file and load it with systemd. Do not commit that file and do not echo it.

```bash
sudo install -d -m 0750 -o relay -g relay /var/lib/ai-passport-relay
sudo install -m 0600 /dev/null /etc/ai-passport-relay.env
sudo editor /etc/ai-passport-relay.env
```

```
RELAY_HOST=0.0.0.0
RELAY_PORT=8788
RELAY_TOKEN=
CALLBACK_SECRET=
TASK_STORE_PATH=/var/lib/ai-passport-relay/tasks.jsonl
STT_URL=https://asrhkchat.kitahim.uk/api/stt
LLM_BASE_URL=https://chat.kitahim.uk
LLM_MODEL=hkgai-v3
TTS_URL=https://tts.kitahim.uk/api/tts
CURSOR_AUTOMATION_URL=
CURSOR_AUTOMATION_TOKEN=
```

Fill the empty secret values locally. Leave a URL line out to keep its default.

### systemd

```ini
[Unit]
Description=AI Passport muse relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=relay
Group=relay
WorkingDirectory=/opt/ai-passport-relay
EnvironmentFile=/etc/ai-passport-relay.env
ExecStart=/opt/ai-passport-relay/.venv/bin/python -m relay
Restart=on-failure
RestartSec=2
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-passport-relay
sudo systemctl status ai-passport-relay
```

### Tests

```bash
pytest
```

The suite is offline. Upstream calls are fakes or a patched opener. Nothing in the tests dials the STT, LLM, TTS, or Cursor hosts.

## Smoke tests

These commands talk to a relay you already started. They use whatever URLs and tokens are in the environment. Do not paste secret values into the shell; export them from the env file.

Tiny 16 kHz mono WAV (0.2 s, 440 Hz):

```bash
python3 - <<'PY'
import math, struct, wave
path = "tiny.wav"
rate = 16000
n = 3200
with wave.open(path, "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    frames = bytearray()
    for i in range(n):
        sample = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<h", sample)
    handle.writeframes(bytes(frames))
print(path)
PY
```

Ask, the way the device does (raw WAV body, bearer token):

```bash
curl -sS -D - \
  -H "Authorization: Bearer ${RELAY_TOKEN}" \
  -H "Content-Type: audio/wav" \
  --data-binary @tiny.wav \
  "http://127.0.0.1:8788/ask" \
  -o reply.bin
```

Decode the muse frames:

```bash
python3 - <<'PY'
import struct
data = open("reply.bin", "rb").read()
i = 0
names = {0: "END", 1: "TEXT", 2: "AUDIO"}
while i + 5 <= len(data):
    kind, length = struct.unpack_from("<BI", data, i)
    i += 5
    payload = data[i:i + length]
    i += length
    if kind == 1:
        print("TEXT", payload.decode("utf-8"))
    else:
        print(names.get(kind, kind), length)
PY
```

Health, last turn, long task, callback:

```bash
curl -sS "http://127.0.0.1:8788/health"
curl -sS "http://127.0.0.1:8788/last"

curl -sS -D - -X POST "http://127.0.0.1:8788/task" \
  -H "Authorization: Bearer ${RELAY_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"input":"note this for later"}'

curl -sS -X POST "http://127.0.0.1:8788/callback" \
  -H "X-Callback-Secret: ${CALLBACK_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"task_id":"TASK_ID","status":"done","summary":"done"}'

curl -sS "http://127.0.0.1:8788/task/TASK_ID"
```

A bad callback secret returns `401` and does not change the task file.

## Deploy on Hermes

Run it on the Hermes host with any Python 3.11+ virtualenv. Bind `0.0.0.0:8788` and let the ESP32 reach that port on the LAN (firewall or `ufw allow 8788/tcp` from the device subnet).

Do not put this process behind Cloudflare. The device uploads a WAV and reads a long streamed body; a proxy will buffer or time out the turn. Point the device at the host's LAN address and port 8788 directly.

Keep `/etc/ai-passport-relay.env` mode `0600`. Keep `TASK_STORE_PATH` on persistent disk. Upstream URLs default to the kitahim hosts above; override them in the env file if those endpoints move. No deploy step publishes this service to Cloudflare.
