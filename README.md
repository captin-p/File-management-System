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

4. Configure PostgreSQL and environment variables in `dms_project/settings.py` or via environment:
   - `POSTGRES_DB`
   - `POSTGRES_USER`
   - `POSTGRES_PASSWORD`
   - `POSTGRES_HOST`
   - `POSTGRES_PORT`

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

## Notes

- Use Django admin to create companies, departments, units, and roles.
- Uploaded documents are stored in `media/storage/`.
- OCR is powered by Tesseract and uses `pdf2image` for PDF rendering.
- The app is designed to be deployable behind Nginx later.
