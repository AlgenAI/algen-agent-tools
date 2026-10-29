# SQLite

Read a SQLite database file through typed tools. Your own named, parameterised queries are
declared in code, exactly as for [PostgreSQL](postgres.md); the model never writes SQL.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.sqlite` |
| Extra | `pip install "algen-agent-tools[sqlite]"` (no extra packages: Python's `sqlite3`) |
| Environment | `ALGEN_TOOLS_SQLITE_PATH` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `sqlite.list_tables` | read | `sqlite.read` | 30 s | Runtime default | agent policy |
| `sqlite.describe_table` | read | `sqlite.read` | 30 s | Runtime default | agent policy |

- `sqlite.list_tables()` lists the database's tables and views. SQLite's internal `sqlite_*`
  tables are left out.
- `sqlite.describe_table(table)` returns each column's name, declared type, whether it can be
  null, its default, and whether it is part of the primary key.

## Your own queries

```python
from pydantic import BaseModel
from algen_agent_tools.sqlite import SqliteToolkit

class Customer(BaseModel):
    customer_id: str

class Invoice(BaseModel):
    id: int
    amount: float

toolkit = SqliteToolkit("billing")
toolkit.query(
    "customer_invoices",
    "SELECT id, amount FROM invoices WHERE customer_id = ? ORDER BY id",
    description="List one customer's invoices.",
    params=Customer,
    row=Invoice,
)
```

- **Placeholders** are plain `?`, filled from the `params` fields in order. Numbered (`?1`) and
  named (`:name`) placeholders are refused, so the order can't silently differ from the model.
  `context_params=("tenant_id", "user_id")` fills the next ones from the call, never from the
  model; scope tool-result caching for such a read to the caller (see
  [PostgreSQL: the caller's rows](postgres.md#the-callers-rows)).
- **Reads** run inside a transaction with `PRAGMA query_only` on, so a declared "read" that
  writes fails. A read that matches more than `max_rows` (default 500) is an error, not a
  silently truncated result.
- **Timeouts:** SQLite has no statement timeout, so a progress handler interrupts any statement
  that runs past the tool's timeout. The connection stays usable afterwards.
- **Writes:** the file opens **read-only by default** (`mode=ro`), and declaring a write query
  fails unless the toolkit is built with `read_only=False`. Each write runs in its own
  `BEGIN IMMEDIATE` transaction. With `idempotency_key=True`, the call's key fills the last
  placeholder, so `ON CONFLICT (request_key) DO NOTHING` makes the write exactly-once. A write
  with `RETURNING` that returns more than `max_rows` rows is rolled back.
- The file must already exist. The connector never creates one.

One connection serves the toolkit, and statements run one at a time in a worker thread. SQLite
suits small, local, read-mostly data. For concurrent writers, use PostgreSQL.

## Agent configuration

```yaml
agents:
  - name: local-data
    domain_plugins: [algen_agent_tools.sqlite]
    enabled_tools: [sqlite.list_tables, sqlite.describe_table]
    tool_permissions: [sqlite.read]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

## The SQL core

PostgreSQL and SQLite share `algen_agent_tools.sql`: `SqlToolkit.query`, the row limit, the
idempotency key and the catalog models. A database adds a `Dialect`, which defines its
placeholders, how it reads without changing anything, how it writes in a transaction, and how it
enforces a timeout.
