from pathlib import Path

from django.core.exceptions import ValidationError


MAX_DOCUMENT_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}
ALLOWED_CONTENT_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/tiff',
}


def validate_document_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError('Upload a PDF or image file (PDF, PNG, JPG, JPEG, TIF, TIFF).')

    if uploaded_file.size > MAX_DOCUMENT_SIZE:
        raise ValidationError('File size must not exceed 10 MB.')

    content_type = getattr(uploaded_file, 'content_type', None)
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError('Unsupported file type.')
