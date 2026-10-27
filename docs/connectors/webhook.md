# Webhook

Send typed events to systems you own: chat incoming-webhooks, automation platforms, your own
services. The deployment fixes **where** each event goes and **what shape** it has. The model
only fills in the fields.

| | |
| --- | --- |
| Module | `algen_agent_tools.webhook` (built in code; there is no plugin) |
| Extra | `pip install "algen-agent-tools[webhook]"` (no extra packages) |
| Environment | one `env://` URL per target, plus an optional signing secret per target |
| Owner | @saurav-samantray |

## Declaring targets

```python
from typing import Literal
from pydantic import BaseModel
from algen_agent_tools.webhook import WebhookTarget, webhook_toolkit

class OpsAlert(BaseModel):
    severity: Literal["info", "warning", "critical"]
    summary: str

toolkit = webhook_toolkit([
    WebhookTarget(
        action="notify_ops",
        url="env://ALGEN_TOOLS_WEBHOOK_OPS_URL",
        signing_secret="env://ALGEN_TOOLS_WEBHOOK_OPS_SECRET",
        payload=OpsAlert,
        description="Post an alert to the operations channel.",
    ),
])
```

Each target becomes one tool, `webhook.<action>`:

| Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- |
| external | `webhook.write` | `timeout_seconds` (30 s) | 1 | agent policy, or always with `approval=True` |

- **The input is the payload model's fields**, closed to unknown fields. The model never supplies
  a URL, headers or a raw body, so this is not a passthrough.
- **The URL** comes from the target's `env://` setting and must be `https`. Set
  `allow_http=True` for a receiver on a private network. URLs with credentials are refused.
  Requests can reach only the configured URLs' hosts, and **redirects are not followed**.
- **The result** is `{"action", "status_code"}`. Any status outside 2xx is an error. The
  receiver's response body is never returned to the model.
- **The URL is treated as a secret**, because incoming-webhook URLs usually embed a token. It
  never appears in an error, and it is redacted from `httpx`'s request log lines.

## What the receiver gets

```http
POST <url>
Content-Type: application/json
Idempotency-Key: <the Runtime's idempotency key for this call>
X-Algen-Signature: t=1792488600,v1=5f2c...   (only with a signing secret)

{"severity":"critical","summary":"disk full"}
```

- **`Idempotency-Key`** is the same on a retry of the same call. Store it, and ignore a key you
  have already processed.
- **`X-Algen-Signature`** is `t=<unix time>,v1=<hex HMAC-SHA256 of "<t>." + raw body>`, keyed
  with the signing secret. Check it against the **raw** body, and reject a timestamp more than a
  few minutes old. In Python:

  ```python
  from algen_agent_tools.webhook import verify

  if not verify(secret, request.headers["X-Algen-Signature"], raw_body):
      return 401
  ```

  In other languages, compute the same HMAC and compare it in constant time.

Bodies are capped at 256 KB.
