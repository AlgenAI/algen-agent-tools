# Security policy

## Supported versions

Algen Agent Tools is pre-release software. Until the first supported release, only the latest
release and the default branch receive security fixes.

| Version | Supported |
| --- | --- |
| Latest release and default branch | Yes |
| Older releases | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
[private vulnerability reporting](https://github.com/AlgenAI/algen-agent-tools/security/advisories/new)
channel. Include the affected version or commit, the connector, impact, reproduction steps and
any suggested remediation. Do not include real customer data or active credentials.

Maintainers will acknowledge a report within three business days and give a status update at
least every seven business days until resolution. Reporters are credited when they ask and when
doing so is safe.

## Security boundaries

Connectors hold vendor credentials and act on external systems for an agent. Each connector:

- reads credentials only through `env://` references, when its client opens;
- exposes narrow, typed actions, never a raw passthrough;
- declares its side effect, permission and idempotency through a Runtime preset, so the
  Runtime's permission, approval and replay controls apply to every call;
- bounds what it returns and keeps errors free of payload content.

Deployers are responsible for least-privilege vendor credentials, network egress controls,
secret storage and the agent's approval policy. A vulnerability in the Runtime itself belongs to
[algen-agent-runtime](https://github.com/AlgenAI/algen-agent-runtime/security).
