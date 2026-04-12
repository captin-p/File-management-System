from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


SEARCH_VECTOR_SQL = """
setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
setweight(to_tsvector('simple', coalesce(description, '')), 'B') ||
setweight(to_tsvector('simple', coalesce(ocr_text, '')), 'C')
"""

TRIGGER_VECTOR_SQL = """
setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
setweight(to_tsvector('simple', coalesce(NEW.description, '')), 'B') ||
setweight(to_tsvector('simple', coalesce(NEW.ocr_text, '')), 'C')
"""


def install_postgres_search_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE OR REPLACE FUNCTION documents_document_search_vector_refresh()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector := {TRIGGER_VECTOR_SQL};
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql;
            """
        )
        cursor.execute(
            """
            DROP TRIGGER IF EXISTS documents_document_search_vector_refresh
            ON documents_document;
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER documents_document_search_vector_refresh
            BEFORE INSERT OR UPDATE OF title, description, ocr_text
            ON documents_document
            FOR EACH ROW EXECUTE FUNCTION documents_document_search_vector_refresh();
            """
        )
        cursor.execute(f"UPDATE documents_document SET search_vector = {SEARCH_VECTOR_SQL};")
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS documents_document_search_vector_gin
            ON documents_document
            USING GIN (search_vector);
            """
        )


def uninstall_postgres_search_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DROP TRIGGER IF EXISTS documents_document_search_vector_refresh
            ON documents_document;
            """
        )
        cursor.execute("DROP FUNCTION IF EXISTS documents_document_search_vector_refresh();")
        cursor.execute("DROP INDEX IF EXISTS documents_document_search_vector_gin;")


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0004_tag_document_document_type_document_tags_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='search_vector',
            field=SearchVectorField(editable=False, null=True),
        ),
        migrations.RunPython(
            install_postgres_search_index,
            reverse_code=uninstall_postgres_search_index,
        ),
    ]
