from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from ..config import settings
from .guardrail import GuardrailError, validate_sql


def scope_healthcare_sql(sql: str, tenant_id: str | None, schema_ctx: dict) -> str:
    if not tenant_id or not tenant_id.strip():
        raise GuardrailError("Healthcare access requires an administrator-provisioned tenant assignment.")
    if settings.warehouse == "databricks":
        dialect = "databricks"
    elif settings.warehouse == "snowflake":
        dialect = "snowflake"
    elif settings.database_url.startswith("postgresql"):
        dialect = "postgres"
    else:
        dialect = "sqlite"

    try:
        tree = parse_one(validate_sql(sql), read=dialect)
    except (ParseError, ValueError) as exc:
        raise GuardrailError(f"Healthcare SQL could not be safely parsed: {exc}") from exc
    if not isinstance(tree, exp.Select) or tree.args.get("with"):
        raise GuardrailError("Healthcare queries must be a single SELECT without common table expressions.")

    tables = list(tree.find_all(exp.Table))
    if not tables:
        raise GuardrailError("Healthcare queries must read an authorized tenant-scoped table.")
    if any(not table.name.lower().startswith("healthcare_") for table in tables):
        raise GuardrailError("Healthcare queries may only read healthcare source tables.")

    protected_columns = {
        "tenant_id",
        "patient_key",
        "encounter_key",
        "diagnosis_key",
        "procedure_key",
        "claim_key",
        "provider_key",
        "facility_key",
        "payer_key",
        "diagnosis_code",
        "procedure_code",
    }
    for select in tree.find_all(exp.Select):
        if not any(
            isinstance(node, exp.AggFunc)
            for projection in select.expressions
            for node in projection.walk()
        ):
            raise GuardrailError("Healthcare queries must return aggregate analytics, not row-level records.")
        group = select.args.get("group")
        if group is not None and any(
            column.name.lower() in protected_columns
            for column in group.find_all(exp.Column)
        ):
            raise GuardrailError("Healthcare queries may not group results by patient or record identifiers.")
        for projection in select.expressions:
            for column in projection.find_all(exp.Column):
                if (
                    column.name.lower() in protected_columns
                    and column.find_ancestor(exp.AggFunc) is None
                ):
                    raise GuardrailError("Healthcare results may not expose patient or record identifiers.")

    schema = {
        str(name).lower(): {str(column.get("name", "")).lower() for column in columns}
        for name, columns in schema_ctx.get("tables", {}).items()
    }
    for table in tables:
        if "tenant_id" not in schema.get(table.name.lower(), set()):
            raise GuardrailError(
                f"Healthcare source '{table.name}' has no verified tenant_id column."
            )
        select = table.find_ancestor(exp.Select)
        if select is None:
            raise GuardrailError("Healthcare query contains a table outside a SELECT scope.")
        alias = table.alias_or_name
        predicate = exp.EQ(
            this=exp.column("tenant_id", table=alias),
            expression=exp.Literal.string(tenant_id),
        )
        join = table.find_ancestor(exp.Join)
        if join is not None and join.this is table:
            if join.args.get("using") or str(join.args.get("side", "")).upper() == "FULL":
                raise GuardrailError("Healthcare joins must use ON and may not use FULL JOIN.")
            condition = join.args.get("on")
            join.set("on", exp.and_(condition, predicate) if condition is not None else predicate)
        else:
            where = select.args.get("where")
            condition = where.this if where is not None else None
            select.set(
                "where",
                exp.Where(this=exp.and_(condition, predicate) if condition is not None else predicate),
            )

    return tree.sql(dialect=dialect)
