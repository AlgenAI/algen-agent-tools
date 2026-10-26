# Sample toolkit

A dependency-free toolkit that calls no external service. Use it to check that an install,
the plugin entry point and `describe` work end to end, and as the template for a new connector.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.sample` |
| Extra | none |
| Environment | none |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Approval |
| --- | --- | --- | --- | --- |
| `sample.echo` | read | `sample.read` | 5 s | agent policy |

`sample.echo(text)` returns `{"text": ..., "length": ...}`. `text` is 1 to 1,000 characters.

## In code

```python
from algen_agent_tools.sample import sample_toolkit

toolkit = sample_toolkit(name="diagnostics")  # registers diagnostics.echo
```
