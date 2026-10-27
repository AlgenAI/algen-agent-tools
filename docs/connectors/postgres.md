# PostgreSQL

Typed actions against one PostgreSQL database. The model never writes SQL: each tool is a named,
parameterised query a developer declares in code, and the model fills in its typed parameters.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.postgres` |
| Extra | `pip install "algen-agent-tools[postgres]"` (asyncpg) |
| Environment | `ALGEN_TOOLS_POSTGRES_DSN` |
| Owner | @saurav-samantray |

## Guarantees

- **No SQL from the model.** Inputs are typed fields; values are always bound parameters.
- **Reads cannot write.** A read query runs in a `READ ONLY` transaction, so PostgreSQL itself
  refuses an `UPDATE` hidden in one.
- **Bounded.** Every statement runs under `SET LOCAL statement_timeout` equal to the tool's
  timeout. Reads stream through a cursor and fail, rather than silently truncate, past the row
  limit (500 by default).
- **Writes are keyed and tried once.** A write preset makes the tool keyed and single-attempt;
  with `idempotency_key=True` the call's idempotency key is bound as the last parameter so the
  SQL can be exactly-once.
- **Catalog reads are limited** to the schemas you list and to tables the role can `SELECT`.

Use a database role with only the privileges the declared queries need.

## Zero-configuration plugin

`algen_agent_tools.postgres` registers two catalog reads against `ALGEN_TOOLS_POSTGRES_DSN`, in
the `public` schema:

| Tool | Side effect | Permission | Returns |
| --- | --- | --- | --- |
| `postgres.list_tables` | read | `postgres.read` | tables and views with approximate row counts |
| `postgres.describe_table` | read | `postgres.read` | columns: type, nullability, default, primary key |

## Declaring typed queries

Most agents need the database's own actions. Declare them in your project's plugin:

```python
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field
from algen_agent_runtime.plugins.sdk import PluginManifest, plugin
from algen_agent_runtime.tools.authoring import write
from algen_agent_tools.postgres import PostgresToolkit


class CustomerInvoices(BaseModel):
    customer_id: str = Field(min_length=1)
    due_before: date


class Invoice(BaseModel):
    id: int
    amount: Decimal
    due: date


class NewNote(BaseModel):
    invoice_id: int
    text: str = Field(max_length=500)


billing = PostgresToolkit("billing", dsn="env://BILLING_DATABASE_URL")

billing.query(
    "list_open_invoices",
    "SELECT id, amount, due FROM invoices "
    "WHERE customer_id = $1 AND status = 'open' AND due < $2 ORDER BY due",
    description="List one customer's open invoices that fall due before a date.",
    params=CustomerInvoices,
    row=Invoice,
)

billing.query(
    "add_note",
    "INSERT INTO invoice_notes (invoice_id, text, request_key) VALUES ($1, $2, $3) "
    "ON CONFLICT (request_key) DO NOTHING",
    description="Add a note to an invoice.",
    params=NewNote,
    preset=write(),
    idempotency_key=True,
)


@plugin(PluginManifest(name="acme.billing", version="1.0.0", publisher="acme"))
class BillingPlugin:
    def register(self, context):
        billing.register(context)
```

- `params` fields fill `$1`, `$2`, ... in field order; the toolkit refuses a query whose
  placeholders do not match. Unknown input fields are rejected.
- A read returns `list[row]`. A write returns `list[row]` when it has `RETURNING`, otherwise
  `{"rows_affected": n}`.
- `preset` is any Runtime preset: `read()` (default), `write(approval=...)`,
  `destructive()` (approval on by default), `long_running(...)`.
- For a read whose result is not a plain list, write your own `@billing.tool(read())` function
  and call `await billing.fetch_rows(sql, values, Row, label="...")`.

`postgres_toolkit(name, dsn=..., schemas=(...))` builds a `PostgresToolkit` that already has the
two catalog reads.

## Testing

```python
from algen_agent_tools.postgres import FakePostgres
from algen_agent_runtime.tools.testing import fake_tool_context

fake = FakePostgres().on("FROM invoices", rows=[{"id": 1, "amount": "9.99", "due": "2026-12-01"}])
billing = PostgresToolkit("billing", client=fake.factory)
```

`FakePostgres` answers by SQL fragment, records every call with its arguments and whether it ran
read-only, and refuses writes inside read-only transactions.

## Replaces

The Runtime's `database_tool(name, handler)` took a raw `statement` from the model. See
[migrating from the Runtime](../migrating-from-runtime.md).
