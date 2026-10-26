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
  plugin.py     # <Connector>Plugin = toolkit_plugin("<connector>", ...)
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
