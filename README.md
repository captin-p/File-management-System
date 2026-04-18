# Document Management System

A core Django-based Document Management System for internal use. This version focuses on a stable foundation first: authentication, document upload, searchable document records, organizational ownership, OCR-backed text extraction, and a responsive Bootstrap UI.

## Project Structure

```text
File-management-System/
|-- accounts/
|   |-- admin.py
|   |-- apps.py
|   |-- migrations/
|   |-- models.py
|   |-- tests.py
|   |-- urls.py
|   `-- views.py
|-- documents/
|   |-- admin.py
|   |-- forms.py
|   |-- migrations/
|   |-- management/
|   |-- models.py
|   |-- services.py
|   |-- storage.py
|   |-- tasks.py
|   |-- tests.py
|   |-- urls.py
|   |-- utils.py
|   |-- validators.py
|   `-- views.py
|-- dms_project/
|   |-- celery.py
|   |-- settings.py
|   |-- urls.py
|   `-- wsgi.py
|-- static/
|   `-- css/
|-- templates/
|   |-- documents/
|   |-- registration/
|   |-- accounts/
|   |-- base.html
|   `-- dashboard.html
|-- media/
|-- manage.py
|-- requirements.txt
`-- .env.example
```

## Core Features

- Login and logout with a custom `User` model built on `AbstractUser`
- Company, department, unit, and role-aware users
- Admin organization settings page for populating and extending department/unit options
- UUID-based `Document` model
- Upload PDF and image files up to 10 MB
- Department and optional unit ownership for each document
- Document type and tag metadata for faceted search
- Local file storage under `media/storage/{department}/{year}/{document_type}/`
- UUID file names with original filename, size, and SHA256 hash tracked in the database
- Background duplicate upload prevention by file hash
- Paginated document list for responsive browsing
- Archive browser by department, year, and document type
- Search by title, description, and OCR text
- Relevance-ranked PostgreSQL full-text search with highlighted matches
- Role-based access by department scope
- Upload-first OCR flow that queues processing and fills metadata after extraction
- OCR metadata extraction for dates, document type, and keyword tags
- Optional OpenAI metadata extraction for titles, headings, summaries, tags, and dates after OCR
- Celery and Redis background processing for OCR and file hashing
- Processing status tracking for queued, ready, and failed documents
- Redis outage fallback that processes uploads inline when the broker is unavailable
- Audit trail for document uploads, views, edits, deletes, and OCR processing
- JSON API for document list and detail access
- Materialized PostgreSQL full-text search index when PostgreSQL is enabled
- SQLite fallback for local development

## Document Model

The `Document` model includes:

- `id` - UUID primary key
- `title`
- `description`
- `file`
- `original_filename`
- `file_size`
- `file_hash`
- `document_type`
- `department`
- `unit`
- `tags`
- `ocr_text`
- `headings`
- `extracted_date`
- `search_vector` - PostgreSQL materialized full-text index field
- `processing_status`
- `ocr_status`
- `ocr_error`
- `metadata_source`
- `metadata_model`
- `uploaded_by`
- `created_at`
- `updated_at`

Document processing is normally queued through Celery. `OCRJob` records remain available for explicit bulk/retry maintenance commands.

Document activity is tracked in `AuditLog` records with actor, action, timestamp, IP address, user agent, and structured metadata.

## File Storage

New uploads are stored below `MEDIA_ROOT` with this layout:

```text
storage/{department}/{year}/{document_type}/{document_uuid}.{extension}
```

The database keeps the original uploaded filename, byte size, and SHA256 hash. Hashing runs in the background task after upload; duplicate files are marked failed and the duplicate stored file is removed. The stored file is re-read after save or relocation to confirm size/hash integrity.

If OCR changes the detected document type during processing, the file is automatically moved from the initial `other` folder into the final document type folder.

## Setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Install OCR and Redis system packages on Linux:

   ```bash
   sudo apt-get update
   sudo apt-get install -y tesseract-ocr poppler-utils redis-server
   ```

4. Copy environment defaults:

   ```bash
   copy .env.example .env
   ```

5. For local development, set `DJANGO_USE_SQLITE=True` in `.env`.

   OCR paths can be left empty when the commands are available on PATH:

   ```env
   OCR_TESSERACT_CMD=
   OCR_POPPLER_PATH=
   ```

   The normal upload flow queues OCR/file hashing in the background and shows the processing status on the document page.

   On Windows, set `OCR_TESSERACT_CMD` if Tesseract is installed but not on PATH:

   ```env
   OCR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

   PDF OCR also requires Poppler. Set `OCR_POPPLER_PATH` to the folder containing `pdftoppm` and `pdfinfo` when those commands are not on PATH.

   To enable AI-assisted heading and metadata extraction after OCR, set:

   ```env
   OPENAI_API_KEY=
   AI_METADATA_ENABLED=True
   AI_METADATA_MODEL=gpt-4.1-mini
   AI_METADATA_USE_IMAGE=True
   AI_METADATA_MAX_OCR_CHARS=12000
   AI_METADATA_MAX_HEADINGS=5
   ```

   The app sends OCR text and, when possible, a first-page image preview to the model. Requests are made with `store=False`, and the existing OCR heuristics remain the fallback when AI is disabled or unavailable.

6. Run migrations:

   ```bash
   python manage.py migrate
   ```

7. Seed the default departments and units:

   ```bash
   python manage.py seed_organization --company-name "Main Company"
   ```

8. Create an admin user:

   ```bash
   python manage.py createsuperuser
   ```

9. Start Redis if it is not already running:

   ```bash
   redis-server
   ```

10. Start the server:

   ```bash
   python manage.py runserver
   ```

11. In a second terminal, start the Celery worker:

   ```bash
   python -m celery -A dms_project worker -l info
   ```

   On Windows, use the solo pool:

   ```bash
   python -m celery -A dms_project worker -l info --pool=solo
   ```

12. Open `http://127.0.0.1:8000/`.

## Organization Seed

Run this command to create the default company, departments, and units:

```bash
python manage.py seed_organization --company-name "Main Company"
```

The command is idempotent. It creates missing rows and leaves existing departments or units untouched.

Admins can also open the dashboard and use `Organization settings` to populate the default departments and units for their assigned company, then add extra departments or units from the web interface.

## Database Configuration

PostgreSQL is the default target. Configure these variables in `.env` for PostgreSQL:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

For local development only, use:

```env
DJANGO_USE_SQLITE=True
```

## Running Tests

```bash
python manage.py test
python manage.py check
```

## API Endpoints

- `GET /api/documents/`
- `GET /api/documents/<uuid>/`

Supported list query parameters:

- `q`
- `tags`
- `document_type`
- `department`
- `unit`
- `ocr_status`
- `page`
- `page_size`

The API uses the same access scope as the HTML interface.

## Background Processing

The upload page saves the file first, queues a Celery task, and redirects to the document detail page. The background task hashes the stored file, rejects duplicates, runs OCR, extracts a date, suggests tags, updates document type/title/description when OCR provides useful text, and marks the document as `ready` or `failed`.

If Redis is temporarily unavailable when a file is uploaded, the app falls back to inline processing for that upload so the system still works. Start Redis and the Celery worker again when available.

Run a Celery worker:

```bash
python -m celery -A dms_project worker -l info
```

On Windows:

```bash
python -m celery -A dms_project worker -l info --pool=solo
```

## OCR Maintenance

OCR date extraction supports common numeric and month-name dates such as `2026-04-13`, `13/04/2026`, `13 April 2026`, and `April 13, 2026`.

When AI metadata extraction is enabled, OCR still extracts the text first. The AI step then uses that OCR text and an optional first-page image preview to identify title-like headings, build a concise description, suggest tags, and capture an extracted date without exposing the full raw OCR dump in the main UI.

The legacy database-backed OCR job commands remain available for bulk imports and retries.

Run one batch of queued OCR jobs:

```bash
python manage.py process_ocr_queue --limit 10
```

Run a long-lived worker:

```bash
python manage.py process_ocr_queue --loop
```

Queue OCR jobs for existing documents:

```bash
python manage.py queue_ocr_jobs
python manage.py queue_ocr_jobs --status failed --status skipped
python manage.py queue_ocr_jobs --document-id <uuid>
```

Re-run OCR synchronously for documents that are pending, failed, or skipped:

```bash
python manage.py reprocess_ocr
```

Target specific statuses or documents:

```bash
python manage.py reprocess_ocr --status failed --status skipped
python manage.py reprocess_ocr --document-id <uuid> --force
```

## Audit Trail

Document detail pages show recent activity for that document. Admin users can inspect the full audit trail in Django admin under `Audit logs`.

Tracked actions include:

- Upload
- View
- Edit
- Delete
- OCR queue
- OCR process

## Search Index Maintenance

PostgreSQL installs a `tsvector` search column, trigger, and GIN index through migrations. The trigger keeps the index text current when document titles, descriptions, or OCR text change.

Keyword searches use the materialized `search_vector` for title, description, and OCR text. PostgreSQL results are ranked by relevance and display highlighted matches. Filters can narrow results by department, unit, document type, OCR status, tags, and extracted document date range.

Rebuild the materialized search vectors after bulk imports or manual database updates:

```bash
python manage.py rebuild_search_index
```

Limit a rebuild when needed:

```bash
python manage.py rebuild_search_index --batch-size 500
python manage.py rebuild_search_index --document-id <uuid>
```

## Notes on Scale

- Documents are listed with pagination to keep response times steady.
- Querysets use `select_related` for uploader and organizational data to reduce extra queries.
- PostgreSQL search uses a materialized weighted `tsvector` column with a GIN index for fast OCR-backed searches.
- SQLite development mode falls back to direct text filtering so the project remains easy to run locally.
- OCR and file hashing run from a Celery worker process, so uploads do not block on expensive PDF/image extraction when Redis is available.
- Database indexes are added on title, file hash, created time, uploader plus created time, department/unit plus created time, OCR status plus created time, processing status plus created time, and document type plus created time.

## Access Rules

- Admins can view, upload, edit, and delete across all departments.
- Managers are scoped to their department and can edit or delete documents in that scope.
- Staff are scoped to their department and can edit only their own documents.
