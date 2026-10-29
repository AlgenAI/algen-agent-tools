"""Named, typed SQL queries shared by every database connector.

The model never supplies SQL. A developer declares each query once with :meth:`SqlToolkit.query`
and the model fills in its typed parameters. A :class:`Dialect` decides how one database runs
it: reads in a read-only transaction, every statement under a timeout, at most a row limit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, get_args

from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import (
    DEFAULT_TIMEOUT_SECONDS,
    Toolkit,
    ToolPreset,
    read,
)
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, create_model

DEFAULT_MAX_ROWS = 500

ContextValue = Literal["tenant_id", "user_id", "run_id", "step_id"]
"""A call's identity a query may bind: set by the Runtime from the run, never by the model."""

CONTEXT_VALUES: tuple[str, ...] = get_args(ContextValue)


class RowsAffected(BaseModel):
    """What a write query that returns no rows reports."""

    rows_affected: int


class TableSummary(BaseModel):
    schema_name: str
    name: str
    kind: Literal["table", "view", "materialized_view"]
    estimated_rows: int | None = None


class ColumnDescription(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    primary_key: bool = False


class TableDescription(BaseModel):
    schema_name: str
    name: str
    columns: list[ColumnDescription]


class _ColumnRow(ColumnDescription):
    schema_name: str | None = None


class TooManyRows(ToolExecutionError):
    """A query matched more rows than its limit."""


class Dialect(ABC):
    """How one database runs the core's reads and writes on the toolkit's client."""

    @abstractmethod
    def placeholder_problem(self, sql: str, expected: int) -> str | None:
        """Why ``sql``'s placeholders do not take ``expected`` values, or ``None``."""

    @abstractmethod
    async def read(
        self,
        client: Any,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]]:
        """Run one read without changing anything; more than ``limit`` rows raises
        :class:`TooManyRows`."""

    @abstractmethod
    async def write(
        self,
        client: Any,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        returning: bool,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]] | int:
        """Run one write in a transaction: its returned rows when ``returning``, else the
        number of rows it changed. More than ``limit`` returned rows rolls it back."""


def too_many_rows(label: str, limit: int, *, rolled_back: bool = False) -> TooManyRows:
    if rolled_back:
        return TooManyRows(
            f"query {label!r} returned more than {limit} rows; the change was rolled back"
        )
    return TooManyRows(f"query {label!r} matched more than {limit} rows; narrow its parameters")


def strict_model(name: str, **fields: Any) -> type[BaseModel]:
    model: type[BaseModel] = create_model(name, __config__=ConfigDict(extra="forbid"), **fields)
    return model


def _forbid_extra(model: type[BaseModel], name: str) -> type[BaseModel]:
    """``model``, refusing unknown fields at validation as its schema already says it does."""
    if model.model_config.get("extra") == "forbid":
        return model
    strict: type[BaseModel] = type(
        f"{name}_arguments", (model,), {"model_config": ConfigDict(extra="forbid")}
    )
    return strict


def row_limit(max_rows: int | None, default: int) -> int:
    if max_rows is None:
        return default
    if max_rows < 1:
        raise ConfigurationError("max_rows must be at least 1")
    return max_rows


class SqlToolkit(Toolkit):
    """A toolkit of named, typed queries against one database, run by ``dialect``."""

    def __init__(
        self,
        name: str,
        *,
        dialect: Dialect,
        max_rows: int = DEFAULT_MAX_ROWS,
        permission_prefix: str | None = None,
        env: tuple[str, ...] = (),
        client: Any,
    ) -> None:
        if max_rows < 1:
            raise ConfigurationError("max_rows must be at least 1")
        super().__init__(name, permission_prefix=permission_prefix, env=env, client=client)
        self.dialect = dialect
        self.max_rows = max_rows

    def check_write_allowed(self, name: str) -> None:
        """Called before a write query is declared; a read-only toolkit refuses."""

    async def fetch_rows[RowT: BaseModel](
        self,
        sql: str,
        values: Sequence[Any],
        row: type[RowT],
        *,
        label: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_rows: int | None = None,
    ) -> list[RowT]:
        """Run one read and return at most ``max_rows`` rows.

        Use it inside your own ``@toolkit.tool(read())`` functions when a tool's result is not
        a plain list of rows. More rows than the limit is an error, not a silent truncation.
        """
        records = await self.dialect.read(
            await self.client(),
            sql,
            values,
            timeout_seconds=timeout_seconds,
            limit=row_limit(max_rows, self.max_rows),
            label=label,
        )
        return [row.model_validate(dict(record)) for record in records]

    def query(
        self,
        name: str,
        sql: str,
        *,
        description: str,
        params: type[BaseModel] | None = None,
        row: type[BaseModel] | None = None,
        preset: ToolPreset | None = None,
        idempotency_key: bool = False,
        max_rows: int | None = None,
        context_params: Sequence[ContextValue] = (),
    ) -> Tool:
        """Declare one query as the tool ``<toolkit>.<name>``.

        ``params`` fields fill the query's placeholders in field order. ``context_params``
        (``tenant_id``, ``user_id``, ``run_id``, ``step_id``) fill the next ones, in the order
        given, from the call's :class:`ToolContext`: the Runtime sets them from the run, and
        they are not part of the tool's input, so the model cannot choose them. Use them to
        keep a query to the caller's own rows. With ``idempotency_key`` the call's idempotency
        key fills the last placeholder, so a write can be made exactly-once. ``row`` is the
        model of each returned row: a read returns ``list[row]``; a write returns ``list[row]``
        when it returns rows, else :class:`RowsAffected`. ``preset`` defaults to ``read()``.

        A read is cacheable, and the Runtime's tool-result cache key does not include the
        ``context_params`` values (algen-agent-runtime#70). Until it does, scope the cache of a
        read that binds ``tenant_id`` or ``user_id`` to ``USER`` or ``RUN``, of one that binds
        ``run_id`` to ``RUN``, and turn caching off for one that binds ``step_id``.
        Otherwise a cached result can reach another caller.
        """
        preset = preset or read()
        reads = preset.side_effect in (SideEffect.NONE, SideEffect.READ)
        params = _forbid_extra(params, name) if params else strict_model(f"{name}_arguments")
        fields = tuple(params.model_fields)
        bound = _context_params(name, context_params, params)
        expected = len(fields) + len(bound) + (1 if idempotency_key else 0)
        problem = self.dialect.placeholder_problem(sql, expected)
        if problem is not None:
            declared = [*fields, *(f"context {value}" for value in bound)]
            raise ConfigurationError(
                f"query {name!r} {problem} but declares {expected} value(s): "
                f"{', '.join(declared) or 'no params'}"
                f"{' and the idempotency key' if idempotency_key else ''}"
            )
        if reads and row is None:
            raise ConfigurationError(f"read query {name!r} needs a row model")
        if reads and idempotency_key:
            raise ConfigurationError(f"read query {name!r} cannot take the idempotency key")
        if not reads:
            self.check_write_allowed(name)
        limit = row_limit(max_rows, self.max_rows)

        async def run(arguments: BaseModel, context: ToolContext) -> Any:
            values = [getattr(arguments, field) for field in fields]
            values.extend(getattr(context, value) for value in bound)
            if idempotency_key:
                values.append(context.idempotency_key)
            if reads:
                assert row is not None
                return await self.fetch_rows(
                    sql,
                    values,
                    row,
                    label=name,
                    timeout_seconds=preset.timeout_seconds,
                    max_rows=limit,
                )
            result = await self.dialect.write(
                await self.client(),
                sql,
                values,
                timeout_seconds=preset.timeout_seconds,
                returning=row is not None,
                limit=limit,
                label=name,
            )
            if row is None:
                assert isinstance(result, int)
                return RowsAffected(rows_affected=result)
            assert isinstance(result, list)
            return [row.model_validate(dict(record)) for record in result]

        run.__name__ = run.__qualname__ = name
        run.__annotations__ = {
            "arguments": params,
            "context": ToolContext,
            "return": list[row] if row is not None else RowsAffected,  # type: ignore[valid-type]
        }
        return self.tool(preset, description=description)(run)

    def add_describe_table(
        self,
        sql: str,
        *,
        arguments: type[BaseModel],
        values: Callable[[BaseModel], list[Any]],
        description: str = (
            "Describe one table or view: each column's name, type, whether it may be null, its\n"
            "default and whether it is part of the primary key."
        ),
    ) -> Tool:
        """Add ``<toolkit>.describe_table``. ``sql`` returns :class:`ColumnDescription` rows for
        the values ``values`` takes from ``arguments``, which has ``table`` and may have
        ``schema_name``; without it, ``sql`` returns each row's ``schema_name`` too."""
        preset = read()

        async def describe_table(arguments: BaseModel) -> TableDescription:
            table: str = getattr(arguments, "table")  # noqa: B009 - a dynamic model's field
            schema: str | None = getattr(arguments, "schema_name", None)
            rows = await self.fetch_rows(
                sql,
                values(arguments),
                _ColumnRow,
                label="describe_table",
                timeout_seconds=preset.timeout_seconds,
            )
            if not rows:
                where = f" in schema {schema!r}" if schema else ""
                raise ToolExecutionError(f"no readable table {table!r}{where}")
            return TableDescription(
                schema_name=schema or rows[0].schema_name or "",
                name=table,
                columns=[
                    ColumnDescription(**row.model_dump(exclude={"schema_name"})) for row in rows
                ],
            )

        describe_table.__doc__ = description
        describe_table.__annotations__ = {"arguments": arguments, "return": TableDescription}
        return self.tool(preset)(describe_table)


def _context_params(
    name: str, requested: Sequence[str], params: type[BaseModel]
) -> tuple[str, ...]:
    """``requested`` checked: known context values, each once, none also a model input name."""
    if isinstance(requested, str):
        raise ConfigurationError(f"query {name!r} context_params must be a sequence of names")
    bound = tuple(requested)
    unknown = [value for value in bound if value not in CONTEXT_VALUES]
    if unknown:
        raise ConfigurationError(
            f"query {name!r} context_params {unknown} are not one of {', '.join(CONTEXT_VALUES)}"
        )
    if len(set(bound)) != len(bound):
        raise ConfigurationError(f"query {name!r} lists a context_params value twice")
    shadowed = sorted(set(bound) & _input_names(params))
    if shadowed:
        raise ConfigurationError(
            f"query {name!r} params field(s) or alias(es) {shadowed} would let the model choose "
            "a value that context_params binds from the call; remove them from params"
        )
    return bound


def _input_names(params: type[BaseModel]) -> set[str]:
    """Every top-level name the model can fill a ``params`` field by: its name and aliases."""
    names: set[str] = set()
    for field_name, field in params.model_fields.items():
        names.add(field_name)
        if field.alias:
            names.add(field.alias)
        aliases = field.validation_alias
        choices = aliases.choices if isinstance(aliases, AliasChoices) else [aliases]
        for choice in choices:
            if isinstance(choice, str):
                names.add(choice)
            elif isinstance(choice, AliasPath) and isinstance(choice.path[0], str):
                names.add(choice.path[0])
    return names


def schema_argument(schemas: tuple[str, ...]) -> tuple[Any, Any]:
    """A ``schema_name`` field limited to ``schemas``, defaulting to the first."""
    if not schemas:
        raise ConfigurationError("schemas must name at least one schema")
    return (
        Literal[schemas],
        Field(default=schemas[0], description=f"One of: {', '.join(schemas)}."),
    )


def table_argument(max_length: int) -> tuple[Any, Any]:
    """A ``table`` field; ``max_length`` is the database's identifier limit."""
    return (
        str,
        Field(min_length=1, max_length=max_length, description="The table or view name."),
    )
