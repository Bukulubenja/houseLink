"""Database-level guarantee that audit events are never updated or deleted (PostgreSQL only).

The ORM guards in models.py stop normal code; this trigger also stops raw SQL, admin bugs and
anyone with app-level DB credentials. Removing it requires a new, reviewed migration.
"""

from django.db import migrations

CREATE_SQL = """
CREATE OR REPLACE FUNCTION audit_auditevent_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditevent_append_only
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_auditevent_block_mutation();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS audit_auditevent_append_only ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_auditevent_block_mutation();
"""


def _run_on_postgres(sql: str):
    def run(apps, schema_editor) -> None:
        if schema_editor.connection.vendor == "postgresql":
            # params=None: send the SQL verbatim. With the default empty params psycopg 3
            # parses `%` (PL/pgSQL's RAISE placeholder) as a query parameter and fails.
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RunPython(_run_on_postgres(CREATE_SQL), _run_on_postgres(DROP_SQL)),
    ]
