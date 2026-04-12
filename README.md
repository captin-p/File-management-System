# Document Management System (DMS)

A local Django-based Document Management System with OCR, department-level access control, document organization, and search.

## Architecture

- `dms_project/`: Django project configuration
- `accounts/`: custom user model, company/department/unit structure, authentication
- `documents/`: document model, OCR processing, search and management views
- `templates/`: Django templates for dashboard, upload, list, detail, edit
- `media/`: local file storage for uploaded documents
- `Dockerfile` / `docker-compose.yml`: containers for local development

## Features

- Login/logout with role-based access (Admin, Manager, Staff)
- Company / Department / Unit hierarchy
- Document upload for PDF/image files with OCR
- OCR text extraction and metadata suggestions
- Folder-style storage under `media/storage/{department}/{year}/{document_type}/`
- Document search by title, tags, OCR content, department, and date
- Archive browsing by department, year, and document type
- Basic API endpoints for document listing and detail
- Secure file handling with department-level document access

## Setup

1. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```

3. Install system dependencies on Linux:
   ```bash
   sudo apt-get update
   sudo apt-get install -y tesseract-ocr poppler-utils
   ```

4. Configure PostgreSQL and environment variables in `dms_project/settings.py` or via environment. PostgreSQL is the production database and should be used for deployment:
   - `POSTGRES_DB`
   - `POSTGRES_USER`
   - `POSTGRES_PASSWORD`
   - `POSTGRES_HOST`
   - `POSTGRES_PORT`

   For local development only, you may optionally set `DJANGO_USE_SQLITE=True`.

5. Run migrations and create a superuser:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

6. Start the development server:
   ```bash
   python manage.py runserver
   ```

7. Open `http://127.0.0.1:8000/` in your browser.

## Docker

```bash
docker compose up --build
```

The web app will be available at `http://127.0.0.1:8000`.

## Production deployment

Create a local `.env` file from `.env.example` and set production values.

Start the production stack with:

```bash
docker compose -f docker-compose.prod.yml up --build
```

Then browse to `http://localhost`.

This setup uses:
- PostgreSQL as the production database
- Gunicorn as the application server
- Nginx as a reverse proxy
- `media/` for uploaded files

## Notes

- Use Django admin to create companies, departments, units, and roles.
- Uploaded documents are stored in `media/storage/`.
- OCR is powered by Tesseract and uses `pdf2image` for PDF rendering.
- The app is designed to be deployable behind Nginx later.
