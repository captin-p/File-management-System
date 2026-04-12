import logging
import os
from pathlib import Path

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .models import Document, OCRJob, OCRJobStatus, OCRStatus

logger = logging.getLogger(__name__)
ACTIVE_OCR_JOB_STATUSES = [OCRJobStatus.QUEUED, OCRJobStatus.PROCESSING]


def _load_ocr_dependencies():
    try:
        from pdf2image import convert_from_path
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        logger.warning('OCR dependencies are unavailable: %s', exc)
        return None

    return {
        'convert_from_path': convert_from_path,
        'Image': Image,
        'pytesseract': pytesseract,
    }


def extract_text_from_image(file_path, dependencies):
    try:
        with dependencies['Image'].open(file_path) as image:
            return dependencies['pytesseract'].image_to_string(image, lang='eng').strip()
    except Exception as exc:
        logger.exception('OCR image extraction failed: %s', exc)
        return ''


def extract_text_from_pdf(file_path, dependencies):
    text = []
    try:
        images = dependencies['convert_from_path'](file_path, dpi=250)
        for page_image in images:
            text.append(dependencies['pytesseract'].image_to_string(page_image, lang='eng'))
    except Exception as exc:
        logger.exception('OCR PDF extraction failed: %s', exc)
    return '\n'.join(text).strip()


def extract_text_from_file(file_path):
    dependencies = _load_ocr_dependencies()
    if not dependencies:
        return None, 'OCR dependencies are not installed.'

    extension = Path(file_path).suffix.lower()
    if extension == '.pdf':
        return extract_text_from_pdf(file_path, dependencies), ''
    if extension in {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}:
        return extract_text_from_image(file_path, dependencies), ''
    return '', ''


def process_document_ocr(document):
    file_path = document.file.path
    if not os.path.exists(file_path):
        logger.warning('Document file not found for OCR: %s', file_path)
        document.ocr_status = OCRStatus.FAILED
        document.ocr_error = 'Uploaded file is missing from storage.'
        document.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])
        return document.ocr_status

    extracted_text, error_message = extract_text_from_file(file_path)
    if extracted_text is None:
        document.ocr_status = OCRStatus.SKIPPED
        document.ocr_error = error_message
        document.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])
        return document.ocr_status

    if extracted_text:
        document.ocr_text = extracted_text
        document.ocr_status = OCRStatus.COMPLETED
        document.ocr_error = ''
    else:
        document.ocr_text = ''
        document.ocr_status = OCRStatus.FAILED
        document.ocr_error = 'OCR did not extract readable text from this file.'

    document.save(update_fields=['ocr_text', 'ocr_status', 'ocr_error', 'updated_at'])
    return document.ocr_status


def enqueue_document_ocr(document, *, force=False):
    if not force:
        existing_job = (
            OCRJob.objects
            .filter(document=document, status__in=ACTIVE_OCR_JOB_STATUSES)
            .order_by('-created_at')
            .first()
        )
        if existing_job:
            return existing_job, False

    if document.ocr_status != OCRStatus.PENDING or document.ocr_error:
        Document.objects.filter(pk=document.pk).update(
            ocr_status=OCRStatus.PENDING,
            ocr_error='',
            updated_at=timezone.now(),
        )
        document.ocr_status = OCRStatus.PENDING
        document.ocr_error = ''

    return OCRJob.objects.create(document=document), True


def _queued_job_queryset(document_id=None):
    queryset = OCRJob.objects.select_related('document').filter(status=OCRJobStatus.QUEUED)
    if document_id:
        queryset = queryset.filter(document_id=document_id)
    return queryset.order_by('created_at')


def claim_next_ocr_job(*, document_id=None):
    with transaction.atomic():
        queryset = _queued_job_queryset(document_id=document_id)
        if getattr(connection.features, 'has_select_for_update_skip_locked', False):
            queryset = queryset.select_for_update(skip_locked=True)
        elif getattr(connection.features, 'has_select_for_update', False):
            queryset = queryset.select_for_update()

        job = queryset.first()
        if not job:
            return None

        now = timezone.now()
        job.status = OCRJobStatus.PROCESSING
        job.attempts = F('attempts') + 1
        job.started_at = now
        job.finished_at = None
        job.error = ''
        job.save(update_fields=['status', 'attempts', 'started_at', 'finished_at', 'error', 'updated_at'])
        job.refresh_from_db()

        Document.objects.filter(pk=job.document_id).update(
            ocr_status=OCRStatus.PROCESSING,
            ocr_error='',
            updated_at=now,
        )
        return job


def process_ocr_job(job):
    document = Document.objects.get(pk=job.document_id)
    try:
        document_status = process_document_ocr(document)
        job.status = (
            OCRJobStatus.COMPLETED
            if document_status == OCRStatus.COMPLETED
            else OCRJobStatus.FAILED
        )
        document.refresh_from_db(fields=['ocr_error'])
        job.error = document.ocr_error
    except Exception as exc:
        logger.exception('Background OCR job failed for document %s', job.document_id)
        message = str(exc) or 'Unexpected OCR processing error.'
        Document.objects.filter(pk=job.document_id).update(
            ocr_status=OCRStatus.FAILED,
            ocr_error=message[:255],
            updated_at=timezone.now(),
        )
        job.status = OCRJobStatus.FAILED
        job.error = message

    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error', 'finished_at', 'updated_at'])
    return job


def process_next_ocr_job(*, document_id=None):
    job = claim_next_ocr_job(document_id=document_id)
    if not job:
        return None
    return process_ocr_job(job)
