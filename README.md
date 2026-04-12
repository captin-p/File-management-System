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
|   |-- tests.py
|   |-- urls.py
|   |-- utils.py
|   |-- validators.py
|   `-- views.py
|-- dms_project/
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
- UUID-based `Document` model
- Upload PDF and image files up to 10 MB
- Department and optional unit ownership for each document
- Document type and tag metadata for faceted search
- Local file storage under `media/storage/{department}/{year}/{document_type}/`
- UUID file names with original filename, size, and SHA256 hash tracked in the database
- Duplicate upload prevention by file hash
- Paginated document list for responsive browsing
- Archive browser by department, year, and document type
- Search by title, description, and OCR text
- Role-based access by department scope
- Upload-first OCR flow that scans files and pre-fills metadata for review
- Background OCR job queue for bulk imports and retry processing
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
- `search_vector` - PostgreSQL materialized full-text index field
- `ocr_status`
- `ocr_error`
- `uploaded_by`
- `created_at`
- `updated_at`

OCR work is tracked in `OCRJob` records with queued, processing, completed, and failed states.

Document activity is tracked in `AuditLog` records with actor, action, timestamp, IP address, user agent, and structured metadata.

## File Storage

New uploads are stored below `MEDIA_ROOT` with this layout:

```text
storage/{department}/{year}/{document_type}/{document_uuid}.{extension}
```

The database keeps the original uploaded filename, byte size, and SHA256 hash. The upload form rejects files whose hash already exists, and the stored file is re-read after save or relocation to confirm size/hash integrity.

If OCR changes the detected document type during upload, the file is automatically moved from the initial `other` folder into the final document type folder.

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

3. Install OCR system packages on Linux:

   ```bash
   sudo apt-get update
   sudo apt-get install -y tesseract-ocr poppler-utils
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

   The normal upload flow scans the file first and opens the metadata form with OCR suggestions.

   On Windows, set `OCR_TESSERACT_CMD` if Tesseract is installed but not on PATH:

   ```env
   OCR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

   PDF OCR also requires Poppler. Set `OCR_POPPLER_PATH` to the folder containing `pdftoppm` and `pdfinfo` when those commands are not on PATH.

6. Run migrations:

   ```bash
   python manage.py migrate
   ```

7. Create an admin user:

   ```bash
   python manage.py createsuperuser
   ```

8. Start the server:

   ```bash
   python manage.py runserver
   ```

9. In a second terminal, start the OCR worker for queued bulk/retry jobs:

   ```bash
   python manage.py process_ocr_queue --loop
   ```

10. Open `http://127.0.0.1:8000/`.

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

## OCR Maintenance

The upload page saves the file first, runs OCR, then opens the metadata form with suggested title, description, document type, and tags. Save that form after review.

Queued OCR jobs are available for bulk imports and retries.

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
- OCR runs from a queue-backed worker process, so uploads do not block on expensive PDF/image extraction.
- Database indexes are added on title, file hash, created time, uploader plus created time, department/unit plus created time, OCR status plus created time, and document type plus created time.

## Access Rules

- Admins can view, upload, edit, and delete across all departments.
- Managers are scoped to their department and can edit or delete documents in that scope.
- Staff are scoped to their department and can edit only their own documents.
