import logging
import os
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .models import DOC_TYPE_CHOICES, Document, OCRJob, OCRJobStatus, OCRStatus, Tag

logger = logging.getLogger(__name__)
ACTIVE_OCR_JOB_STATUSES = [OCRJobStatus.QUEUED, OCRJobStatus.PROCESSING]
TAG_STOP_WORDS = {
    'about',
    'after',
    'also',
    'amount',
    'and',
    'are',
    'because',
    'been',
    'before',
    'document',
    'from',
    'have',
    'into',
    'invoice',
    'page',
    'pages',
    'payment',
    'report',
    'scan',
    'scanned',
    'that',
    'the',
    'this',
    'total',
    'with',
    'your',
}
DOCUMENT_TYPE_KEYWORDS = {
    'invoice': {'invoice', 'receipt', 'subtotal', 'tax', 'amount due', 'bill to', 'payment'},
    'contract': {'agreement', 'contract', 'party', 'parties', 'terms', 'signature'},
    'policy': {'policy', 'procedure', 'compliance', 'approved', 'guideline'},
    'report': {'report', 'summary', 'analysis', 'findings', 'quarterly', 'annual'},
    'memo': {'memo', 'memorandum', 'notice', 'attention', 'subject'},
}


def _configure_tesseract(pytesseract):
    configured_cmd = getattr(settings, 'OCR_TESSERACT_CMD', '')
    if configured_cmd:
        if os.path.exists(configured_cmd):
            pytesseract.pytesseract.tesseract_cmd = configured_cmd
            return ''
        return f'Tesseract was not found at OCR_TESSERACT_CMD={configured_cmd}.'

    if shutil.which('tesseract'):
        return ''

    return 'Tesseract is not installed or is not available on PATH.'


def _load_ocr_dependencies():
    try:
        from pdf2image import convert_from_path
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        logger.warning('OCR dependencies are unavailable: %s', exc)
        return None, 'OCR Python dependencies are not installed.'

    tesseract_error = _configure_tesseract(pytesseract)
    if tesseract_error:
        logger.warning(tesseract_error)
        return None, tesseract_error

    return (
        {
            'convert_from_path': convert_from_path,
            'Image': Image,
            'pytesseract': pytesseract,
        },
        '',
    )


def extract_text_from_image(file_path, dependencies):
    try:
        with dependencies['Image'].open(file_path) as image:
            return dependencies['pytesseract'].image_to_string(image, lang='eng').strip()
    except Exception as exc:
        logger.exception('OCR image extraction failed: %s', exc)
        return ''


def _validate_poppler_for_pdf():
    poppler_path = getattr(settings, 'OCR_POPPLER_PATH', '')
    if poppler_path:
        executable_name = 'pdftoppm.exe' if os.name == 'nt' else 'pdftoppm'
        executable_path = os.path.join(poppler_path, executable_name)
        if os.path.exists(executable_path):
            return ''
        return f'Poppler was not found at OCR_POPPLER_PATH={poppler_path}.'

    if shutil.which('pdftoppm') and shutil.which('pdfinfo'):
        return ''

    return 'Poppler is not installed or is not available on PATH. PDF OCR cannot run without Poppler.'


def extract_text_from_pdf(file_path, dependencies):
    text = []
    try:
        convert_kwargs = {'dpi': 250}
        poppler_path = getattr(settings, 'OCR_POPPLER_PATH', '')
        if poppler_path:
            convert_kwargs['poppler_path'] = poppler_path
        images = dependencies['convert_from_path'](file_path, **convert_kwargs)
        for page_image in images:
            text.append(dependencies['pytesseract'].image_to_string(page_image, lang='eng'))
    except Exception as exc:
        logger.exception('OCR PDF extraction failed: %s', exc)
    return '\n'.join(text).strip()


def extract_text_from_file(file_path):
    dependencies, dependency_error = _load_ocr_dependencies()
    if not dependencies:
        return None, dependency_error

    extension = Path(file_path).suffix.lower()
    if extension == '.pdf':
        poppler_error = _validate_poppler_for_pdf()
        if poppler_error:
            return None, poppler_error
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


def suggested_title_from_ocr(ocr_text, fallback):
    for line in ocr_text.splitlines():
        cleaned = ' '.join(line.strip().split())
        if len(cleaned) >= 4:
            return cleaned[:255]
    return fallback[:255] or 'Scanned document'


def suggested_description_from_ocr(ocr_text):
    cleaned = ' '.join(ocr_text.split())
    return cleaned[:600]


def suggested_document_type_from_ocr(ocr_text, fallback='other'):
    haystack = ocr_text.lower()
    scores = {}
    for document_type, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        scores[document_type] = sum(1 for keyword in keywords if keyword in haystack)

    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score:
        return best_type
    return fallback if fallback in dict(DOC_TYPE_CHOICES) else 'other'


def suggested_tags_from_ocr(ocr_text, *, limit=6):
    words = re.findall(r'[a-zA-Z][a-zA-Z0-9-]{2,}', ocr_text.lower())
    counts = {}
    for word in words:
        if word in TAG_STOP_WORDS:
            continue
        counts[word] = counts.get(word, 0) + 1

    ranked_words = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked_words[:limit]]


def apply_ocr_metadata_suggestions(document):
    document.refresh_from_db()
    fallback_title = Path(document.file.name).stem.replace('-', ' ').replace('_', ' ')
    ocr_text = document.ocr_text or ''
    if not ocr_text.strip():
        if not document.title:
            document.title = suggested_title_from_ocr('', fallback_title)
            document.save(update_fields=['title', 'updated_at'])
        return {'title': document.title, 'description': document.description, 'document_type': document.document_type, 'tags': []}

    document.title = suggested_title_from_ocr(ocr_text, fallback_title)
    document.description = suggested_description_from_ocr(ocr_text)
    document.document_type = suggested_document_type_from_ocr(ocr_text, document.document_type)
    document.save(update_fields=['title', 'description', 'document_type', 'updated_at'])

    tag_names = suggested_tags_from_ocr(ocr_text)
    if tag_names:
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]
        document.tags.set(tags)

    return {
        'title': document.title,
        'description': document.description,
        'document_type': document.document_type,
        'tags': tag_names,
    }


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
