
# app/warehouse.py

from functools import lru_cache

# Only backend-controlled mappings are allowed.
DOMAIN_CATALOGS = {
    "banking": "banking",
    "finance": "banking",
    "insurance": "insurance",
    "retail": "retail",
}

DOMAIN_SCHEMA = "gold"


def get_authorized_namespace(domain: str) -> tuple[str, str]:
    domain = domain.strip().lower()

    catalog = DOMAIN_CATALOGS.get(domain)
    if not catalog:
        raise ValueError(f"Unsupported domain: {domain}")

    return catalog, DOMAIN_SCHEMA


def discover_gold_metadata(domain: str) -> list[dict]:
    """
    Discover actual tables and columns from the authorized
    Databricks catalog/schema. Never accept catalog names
    directly from the user's prompt.
    """
    catalog, schema = get_authorized_namespace(domain)

    # Import and use your existing Databricks connection helper.
    # Replace this import with the actual helper in your project.
    from app.warehouse import get_databricks_connection

    connection = get_databricks_connection()
    metadata = []

    try:
        cursor = connection.cursor()

        # Catalog/schema are selected from the trusted mapping.
        cursor.execute(f"SHOW TABLES IN `{catalog}`.`{schema}`")
        tables = cursor.fetchall()

        for row in tables:
            table_name = row[1]

            # Table name came from Databricks metadata, not user input.
            cursor.execute(
                f"DESCRIBE TABLE "
                f"`{catalog}`.`{schema}`.`{table_name}`"
            )

            columns = []
            for col in cursor.fetchall():
                name = col[0]
                data_type = col[1] if len(col) > 1 else ""

                # Ignore non-column rows such as DESCRIBE section headers.
                if name and data_type and not name.startswith("#"):
                    columns.append({
                        "name": name,
                        "type": data_type,
                    })

            metadata.append({
                "catalog": catalog,
                "schema": schema,
                "table": table_name,
                "columns": columns,
            })

        cursor.close()
    finally:
        connection.close()

    return metadata

def generate_sql_for_question(domain: str, question: str) -> str:
    metadata = discover_gold_metadata(domain)

    if not metadata:
        raise ValueError(
            f"No Gold tables found for domain: {domain}"
        )

    # Send both the question and metadata to your configured LLM.
    # Implement this using the LLM client already used by
    # app/services/sql_generator.py.
    sql = call_your_existing_llm(
        question=question,
        schema_metadata=metadata,
        instructions=(
            "Generate one Databricks SQL SELECT query. "
            "Use only the supplied tables and columns. "
            "Use fully qualified catalog.schema.table names. "
            "Do not invent columns or tables. "
            "Do not generate INSERT, UPDATE, DELETE, DROP, "
            "CREATE, ALTER, or other modifying statements."
        ),
    )

    return validate_read_only_sql(sql, domain, metadata)