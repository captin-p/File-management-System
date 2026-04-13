import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .models import AuditAction, AuditLog, DOC_TYPE_CHOICES, Document, OCRJob, OCRJobStatus, OCRStatus, Tag

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
    'date',
    'document',
    'from',
    'have',
    'issued',
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
MONTH_ALIASES = {
    'jan': 1,
    'january': 1,
    'feb': 2,
    'february': 2,
    'mar': 3,
    'march': 3,
    'apr': 4,
    'april': 4,
    'may': 5,
    'jun': 6,
    'june': 6,
    'jul': 7,
    'july': 7,
    'aug': 8,
    'august': 8,
    'sep': 9,
    'sept': 9,
    'september': 9,
    'oct': 10,
    'october': 10,
    'nov': 11,
    'november': 11,
    'dec': 12,
    'december': 12,
}
DATE_PATTERNS = [
    re.compile(r'\b(?P<year>(?:19|20)\d{2})[-/.](?P<month>0?[1-9]|1[0-2])[-/.](?P<day>0?[1-9]|[12]\d|3[01])\b'),
    re.compile(r'\b(?P<day>0?[1-9]|[12]\d|3[01])[-/.](?P<month>0?[1-9]|1[0-2])[-/.](?P<year>(?:19|20)\d{2})\b'),
    re.compile(
        r'\b(?P<day>0?[1-9]|[12]\d|3[01])\s+'
        r'(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        r',?\s+(?P<year>(?:19|20)\d{2})\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        r'\s+(?P<day>0?[1-9]|[12]\d|3[01]),?\s+(?P<year>(?:19|20)\d{2})\b',
        re.IGNORECASE,
    ),
]


def _request_ip_address(request):
    if not request:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_document_audit(action, *, document=None, user=None, request=None, message='', metadata=None):
    actor = user if getattr(user, 'is_authenticated', False) else None
    user_agent = ''
    if request:
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]

    return AuditLog.objects.create(
        document=document,
        actor=actor,
        action=action,
        message=message,
        metadata=metadata or {},
        ip_address=_request_ip_address(request),
        user_agent=user_agent,
    )


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
        document.extracted_date = suggested_extracted_date_from_ocr(extracted_text)
        document.ocr_status = OCRStatus.COMPLETED
        document.ocr_error = ''
    else:
        document.ocr_text = ''
        document.extracted_date = None
        document.ocr_status = OCRStatus.FAILED
        document.ocr_error = 'OCR did not extract readable text from this file.'

    document.save(update_fields=['ocr_text', 'extracted_date', 'ocr_status', 'ocr_error', 'updated_at'])
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


def _date_from_parts(year, month, day):
    try:
        month_value = MONTH_ALIASES.get(str(month).lower(), month)
        return date(int(year), int(month_value), int(day))
    except (TypeError, ValueError):
        return None


def extracted_dates_from_ocr(ocr_text):
    matches = []
    seen = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(ocr_text):
            candidate = _date_from_parts(
                match.group('year'),
                match.group('month'),
                match.group('day'),
            )
            if candidate and candidate not in seen:
                seen.add(candidate)
                matches.append((match.start(), candidate))
    return [candidate for _, candidate in sorted(matches, key=lambda item: item[0])]


def suggested_extracted_date_from_ocr(ocr_text):
    dates = extracted_dates_from_ocr(ocr_text)
    return dates[0] if dates else None


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
        return {
            'title': document.title,
            'description': document.description,
            'document_type': document.document_type,
            'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
            'tags': [],
        }

    document.title = suggested_title_from_ocr(ocr_text, fallback_title)
    document.description = suggested_description_from_ocr(ocr_text)
    document.document_type = suggested_document_type_from_ocr(ocr_text, document.document_type)
    document.extracted_date = suggested_extracted_date_from_ocr(ocr_text)
    document.save(update_fields=['title', 'description', 'document_type', 'extracted_date', 'updated_at'])

    tag_names = suggested_tags_from_ocr(ocr_text)
    if tag_names:
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]
        document.tags.set(tags)

    return {
        'title': document.title,
        'description': document.description,
        'document_type': document.document_type,
        'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
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

    job = OCRJob.objects.create(document=document)
    log_document_audit(
        AuditAction.OCR_QUEUE,
        document=document,
        message='OCR job queued.',
        metadata={'job_id': str(job.pk), 'force': force},
    )
    return job, True


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
    log_document_audit(
        AuditAction.OCR_PROCESS,
        document=document,
        message='OCR job processed.',
        metadata={
            'job_id': str(job.pk),
            'job_status': job.status,
            'document_ocr_status': document.ocr_status,
            'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
            'attempts': job.attempts,
        },
    )
    return job


def process_next_ocr_job(*, document_id=None):
    job = claim_next_ocr_job(document_id=document_id)
    if not job:
        return None
    return process_ocr_job(job)
