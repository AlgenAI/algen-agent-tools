# Writing a connector

Read the [connector policy](connector-policy.md) first; a connector needs an accepted proposal.
`src/algen_agent_tools/sample` is the smallest complete connector to copy.

## Layout

```text
src/algen_agent_tools/<connector>/
  __init__.py   # public API: the toolkit factory, its models and its fake
  toolkit.py    # <connector>_toolkit(...) -> Toolkit
  client.py     # the vendor client, if any; optional imports happen here, lazily
  fake.py       # an in-memory stand-in for the client, used by tests and by users' tests
  plugin.py     # INFO = ConnectorInfo(...); <Connector>Plugin = toolkit_plugin(INFO, ...)
tests/unit/test_<connector>.py          # offline: contract, every tool against the fake
tests/integration/test_<connector>.py   # live or container tests, marked live or integration
docs/connectors/<connector>.md
```

And in `pyproject.toml`:

```toml
[project.optional-dependencies]
<connector> = ["vendor-sdk>=1.2,<2"]

[project.entry-points."algen_agent_runtime.plugins"]
"algen_agent_tools.<connector>" = "algen_agent_tools.<connector>.plugin:<Connector>Plugin"
```

## The toolkit factory

```python
def crm_toolkit(
    name: str = "crm",
    *,
    token: str = "env://ALGEN_TOOLS_CRM_TOKEN",
    client: ClientFactory | None = None,
) -> Toolkit:
    toolkit = Toolkit(name, env=env_names(token), client=client or _opener(token))

    @toolkit.tool(read())
    async def find_account(domain: str) -> Account:
        """Find one CRM account by its web domain."""
        crm: CrmClient = await toolkit.client()
        return await crm.find(domain)

    return toolkit
```

- The factory takes every setting as an argument, with `env://` defaults named
  `ALGEN_TOOLS_<CONNECTOR>_<SETTING>`, and a `client` override so tests can pass the fake.
- Tools are defined inside the factory, so each call returns an independent toolkit.
- Nothing connects in the factory or in `register`; the client opens at container start or on
  first use, and resolves its secrets then.
- The docstring is what the model reads: say what the action does and when to use it.
- Declare id fields, in inputs and results, as identifiers: a chat, message, thread,
  attachment or page id, or an order or tracking number, that may be a digit string. Use
  `id_field(pattern)` from `algen_agent_tools.attachments` for an input id, or
  `Field(json_schema_extra={"format": "identifier"})`. The Runtime's `pii` guardrail (0.2.0b2 and
  later) then leaves a one-token value there alone; without it a ten-digit chat id can read as a
  phone number, and a redacted argument refuses the call. Address fields declare
  `{"format": "email"}` for the same reason. Earlier Runtimes ignore both. Never mark free text
  (a message, subject, query or name): it is what the guardrail screens.

### Using what the Runtime already does

Since 0.2.0a13 the package needs Runtime 0.2.0b5. Use its tool semantics instead of building
your own (the Runtime's `docs/tool-authoring.md` has the details):

- **Refused before sending.** Raise `ToolNotAttemptedError` or `PolicyDeniedError` (or wrap the
  connector's own error with `algen_agent_tools._core.errors.not_attempted`) only when nothing
  left the process, or the provider definitely rejected the request (`rejected(status)`). The
  ledger then records the call `failed`, so a write needs no reconciliation and can be retried.
- **A failure the model should fix.** When the remote system ran the action and reported that
  it failed (a validation message, MCP `isError`), return it as data:
  `ToolResult(value={..., "error": True}, failed=True)`, or a result model with
  `failure_field="ok"`. The call is recorded failed, never cached and not retried, and the model
  reads why. The output schema must allow the failure payload.
- **State that changes on its own.** A listing of an inbox, a chat or a thread is
  `volatile=True`, so the tool-result cache never serves it and the repeat guard never holds back
  a second identical call.
- **Results that depend on the call.** A tool whose result depends on its `ToolContext` declares
  `cache_context`: `{"run_id"}` (`algen_agent_tools.attachments.tool.RUN_SCOPED`) when it stores
  or loads an artifact through an `ArtifactSink` or `ArtifactLoader`, the bound values for SQL
  `context_params`. Otherwise a cached result skips the loader's access check.
- **Timeouts and cancellation.** The Runtime cancels an async handler at its timeout or when the
  run is cancelled; do not add a deadline of your own on top of the tool's `timeout_seconds`
  except where the protocol needs one (a statement timeout on the server). Blocking client
  libraries run through `asyncio.to_thread`; a connection only one thread may use goes through
  `algen_agent_tools._core.threads.run_exclusive`, which keeps it locked until the worker thread
  ends after a cancelled call.
- **Concurrency.** `max_concurrency` on a tool caps that tool's calls in flight. A client shared
  by several tools of a toolkit serializes itself (a lock), since the Runtime's cap is per tool.
- **Credentials.** Connectors act with the deployment's `env://` credentials, not with
  `context.credentials` (the signed-in caller's): a connector that acts as the user needs its own
  review. Mask any credential a remote service could echo back with
  `algen_agent_runtime.security.redact_held` before returning it.

When a connector belongs to a family, build on the family's shared tools instead of writing
them again, so every platform offers the same names and inputs:

- Mail: `algen_agent_tools.mail` (`add_send_tool`, `add_mailbox_read_tools`).
- Chat: `algen_agent_tools.messaging`. Declare a `MessagingPlatform` with its `Capability`
  flags, implement a `MessagingProvider`, and call `messaging_toolkit`. The Telegram connector
  is the example to copy.
- Databases: `algen_agent_tools.sql`. Implement a `Dialect` and subclass `SqlToolkit`; the
  PostgreSQL, MySQL and SQLite connectors are the examples.
- Files: `algen_agent_tools.attachments` (`add_get_attachment_tool`, `AttachmentPolicy`).

## The plugin and its catalog entry

The plugin declares what the connector is and needs, as data. Hosts such as Studio read it from
the package's connector catalog (`algen_agent_tools/connectors.json`) to list the connector,
collect its settings and turn it on for an agent, without running connector code.

```python
INFO = ConnectorInfo(
    connector="crm",
    title="Acme CRM",
    summary="Find accounts in Acme CRM by web domain.",
    category="utility",
    extra="crm",
    settings=(
        Setting(env="ALGEN_TOOLS_CRM_TOKEN", description="An API token.", secret=True),
        Setting(env="ALGEN_TOOLS_CRM_REGION", description="Data region.", required=False,
                default="us"),
    ),
    egress=(Egress(protocol="https", host="api.acme-crm.example", port=443),),
    code_only_options=("client",),
)

CrmPlugin = toolkit_plugin(INFO, crm_toolkit)
```

- `settings` lists every variable the zero-configuration toolkit reads; the plugin manifest's
  `env` comes from it. Mark credentials `secret=True`.
- `egress` names every host the connector connects to, fixed or taken from a setting
  (`host_setting`), so a host can build its network allowlist.
- `code_only_options` names factory options a configuration-only host cannot set yet. Keep it
  short: prefer a setting to a Python-only option.

Then regenerate the catalog and commit it with the change:

```bash
python -m algen_agent_tools catalog --write
```

The catalog records every tool's name, input schema, side effect and permissions, so it is also
the package's contract snapshot: a test fails when it drifts from the code, and the diff shows
reviewers exactly which public contract changed. Such a change needs a CHANGELOG entry.

## Tests

Each connector's unit tests must:

- call `assert_toolkit_contract(<connector>_toolkit(client=fake.factory))`;
- run every tool against the fake, including the failure paths;
- check `plugin_tools("algen_agent_tools.<connector>")` lists the expected tools, side effects
  and permissions;
- test the missing-extra error with `monkeypatch.setitem(sys.modules, "<package>", None)`.

Live tests are marked `@pytest.mark.live` and read credentials from the environment; they run
only with `ALGEN_TOOLS_LIVE=1` and never in pull-request CI. Tests against a local service
container are marked `@pytest.mark.integration`.
