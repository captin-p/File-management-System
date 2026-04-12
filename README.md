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
|   |-- models.py
|   |-- tests.py
|   |-- urls.py
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
- Local file storage under `media/documents/`
- Paginated document list for responsive browsing
- Search by title, description, and OCR text
- Role-based access by department scope
- Automatic OCR processing for PDF and image uploads with status tracking
- PostgreSQL-aware full-text ranking when PostgreSQL is enabled
- SQLite fallback for local development

## Document Model

The `Document` model includes:

- `id` - UUID primary key
- `title`
- `description`
- `file`
- `department`
- `unit`
- `ocr_text`
- `ocr_status`
- `ocr_error`
- `uploaded_by`
- `created_at`
- `updated_at`

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

9. Open `http://127.0.0.1:8000/`.

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

## Notes on Scale

- Documents are listed with pagination to keep response times steady.
- Querysets use `select_related` for uploader and organizational data to reduce extra queries.
- PostgreSQL search uses weighted full-text ranking when available.
- Database indexes are added on title, created time, uploader plus created time, department/unit plus created time, and OCR status plus created time.

## Access Rules

- Admins can view, upload, edit, and delete across all departments.
- Managers are scoped to their department and can edit or delete documents in that scope.
- Staff are scoped to their department and can edit only their own documents.
