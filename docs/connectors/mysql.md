# MySQL / MariaDB

Read a MySQL or MariaDB database through typed tools. Your own named, parameterised queries are
declared in code, exactly as for [PostgreSQL](postgres.md); the model never writes SQL.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.mysql` |
| Extra | `pip install "algen-agent-tools[mysql]"` (`aiomysql` with `cryptography`, which MySQL 8's default authentication needs) |
| Environment | `ALGEN_TOOLS_MYSQL_DSN` (secret) |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `mysql.list_tables` | read | `mysql.read` | 30 s | Runtime default | agent policy |
| `mysql.describe_table` | read | `mysql.read` | 30 s | Runtime default | agent policy |

Both tools work on the DSN's database: `list_tables()` lists the tables and views you can read,
with approximate row counts, and `describe_table(table)` describes one table's columns.

## Connection

`mysql://user:password@host:port/database`. Add `?ssl=verify` for TLS with certificate and
hostname verification; no other options are accepted. Give the user only the privileges the
declared queries need: `SELECT` for a read-only agent.

## Your own queries

```python
from algen_agent_tools.mysql import MysqlToolkit

toolkit = MysqlToolkit("billing")
toolkit.query(
    "customer_invoices",
    "SELECT id, amount FROM invoices WHERE customer_id = %s AND note NOT LIKE 'test%%'",
    description="List one customer's invoices.",
    params=Customer,
    row=Invoice,
)
```

- **Placeholders** are `%s`, filled from the `params` fields in order, and a literal `%` is
  written `%%`. Named placeholders and a stray `%` are refused when the query is declared, not
  at the first call. `context_params=("tenant_id", "user_id")` fills the next ones from the
  call, never from the model, and the tool-result cache keys on them (see
  [PostgreSQL: the caller's rows](postgres.md#the-callers-rows)).
- **Reads** run in `START TRANSACTION READ ONLY`, so a declared "read" that writes fails. More
  rows than `max_rows` (default 500) is an error, not a truncated result.
- **Writes** run in their own transaction. With `idempotency_key=True` the call's key fills the
  last placeholder, so `INSERT IGNORE` or `ON DUPLICATE KEY UPDATE` on a unique key makes the
  write exactly-once. On MariaDB, a write with `RETURNING` that returns more than `max_rows`
  rows is rolled back.
- **Timeouts.** Each statement is limited to the tool's timeout in three ways:
  - **On the server:** MySQL's `max_execution_time` applies to reads, and MariaDB's
    `max_statement_time` applies to every statement. The connector detects which server it's
    talking to when it connects.
  - **Lock waits:** `innodb_lock_wait_timeout` is set to the tool's timeout.
  - **On the client:** a deadline one second later drops the connection. The pool discards it,
    and the server rolls back its open transaction.
- **Errors** name the query and the MySQL error number only. The server's message can quote the
  data (for example, a duplicate value), so it is never returned.

## Agent configuration

```yaml
agents:
  - name: reporting
    domain_plugins: [algen_agent_tools.mysql]
    enabled_tools: [mysql.list_tables, mysql.describe_table]
    tool_permissions: [mysql.read]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

## Testing

`algen_agent_tools.mysql.FakeMysql` answers statements by SQL fragment and records every call,
including whether it ran in a read-only transaction. A write inside a read-only transaction
fails, as it does on the server. CI also runs the integration tests against MySQL 8.4.
