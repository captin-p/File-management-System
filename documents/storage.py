import hashlib
import posixpath
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import slugify


STORAGE_ROOT = 'storage'
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class DocumentFileMetadata:
    original_filename: str
    file_size: int
    file_hash: str


def original_filename_from_upload(uploaded_file):
    name = str(getattr(uploaded_file, 'name', '') or '')
    return name.replace('\\', '/').rsplit('/', 1)[-1][:255]


def storage_segment(value, fallback):
    segment = slugify(str(value or '').strip())
    return (segment or fallback)[:80]


def storage_department_segment(document):
    department = getattr(document, 'department', None)
    return storage_segment(
        getattr(department, 'slug', '') or getattr(department, 'name', ''),
        'unassigned',
    )


def storage_document_type_segment(document):
    return storage_segment(getattr(document, 'document_type', '') or 'other', 'other')


def storage_year(document):
    created_at = getattr(document, 'created_at', None)
    return created_at.year if created_at else timezone.now().year


def document_uuid_filename(document, source_filename):
    extension = Path(source_filename or '').suffix.lower()
    document_id = getattr(document, 'id', None) or uuid.uuid4()
    return f'{document_id}{extension}'


def document_storage_directory(document):
    return posixpath.join(
        STORAGE_ROOT,
        storage_department_segment(document),
        str(storage_year(document)),
        storage_document_type_segment(document),
    )


def document_file_upload_path(document, filename):
    return posixpath.join(
        document_storage_directory(document),
        document_uuid_filename(document, filename),
    )


def calculate_file_metadata(uploaded_file, *, original_filename=None):
    digest = hashlib.sha256()
    file_size = 0
    position = None

    try:
        position = uploaded_file.tell()
    except (AttributeError, OSError):
        position = None

    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass

    if hasattr(uploaded_file, 'chunks'):
        chunks = uploaded_file.chunks()
    else:
        chunks = iter(lambda: uploaded_file.read(HASH_CHUNK_SIZE), b'')

    for chunk in chunks:
        if isinstance(chunk, str):
            chunk = chunk.encode()
        file_size += len(chunk)
        digest.update(chunk)

    try:
        uploaded_file.seek(position or 0)
    except (AttributeError, OSError):
        pass

    return DocumentFileMetadata(
        original_filename=(original_filename or original_filename_from_upload(uploaded_file)),
        file_size=file_size,
        file_hash=digest.hexdigest(),
    )


def apply_file_metadata(document, uploaded_file, *, metadata=None):
    metadata = metadata or calculate_file_metadata(uploaded_file)
    document.original_filename = metadata.original_filename
    document.file_size = metadata.file_size
    document.file_hash = metadata.file_hash
    return metadata


def apply_file_basics(document, uploaded_file):
    document.original_filename = original_filename_from_upload(uploaded_file)
    document.file_size = getattr(uploaded_file, 'size', None)
    return document.original_filename, document.file_size


def stored_file_metadata(document):
    if not document.file:
        raise ValidationError('Document has no stored file.')

    try:
        with default_storage.open(document.file.name, 'rb') as stored_file:
            return calculate_file_metadata(
                stored_file,
                original_filename=document.original_filename or Path(document.file.name).name,
            )
    except FileNotFoundError as exc:
        raise ValidationError('Stored file is missing.') from exc


def validate_document_file_integrity(document):
    if not document.file_hash and document.file_size is None:
        return True

    metadata = stored_file_metadata(document)
    if document.file_hash and metadata.file_hash != document.file_hash:
        raise ValidationError('Stored file hash does not match the uploaded file hash.')
    if document.file_size is not None and metadata.file_size != document.file_size:
        raise ValidationError('Stored file size does not match the uploaded file size.')
    return True


def relocate_document_file(document):
    if not document.file:
        return False

    current_name = document.file.name
    target_name = document_file_upload_path(
        document,
        document.original_filename or Path(current_name).name,
    )

    if current_name == target_name:
        return False

    if default_storage.exists(target_name):
        raise ValidationError('A file already exists at the expected storage path.')

    document.file.open('rb')
    try:
        saved_name = default_storage.save(target_name, File(document.file))
    finally:
        document.file.close()

    if saved_name != target_name:
        default_storage.delete(saved_name)
        raise ValidationError('Storage could not reserve the expected UUID filename.')

    default_storage.delete(current_name)
    document.file.name = saved_name
    document.__class__.objects.filter(pk=document.pk).update(
        file=saved_name,
        updated_at=timezone.now(),
    )
    validate_document_file_integrity(document)
    return True
