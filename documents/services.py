import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone
from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from .models import (
    AuditAction,
    AuditLog,
    DOC_TYPE_CHOICES,
    Document,
    MetadataSource,
    DocumentProcessingStatus,
    OCRJob,
    OCRJobStatus,
    OCRStatus,
    Tag,
)
from .ai_extraction import extract_document_metadata_with_ai
from .storage import relocate_document_file, stored_file_metadata, validate_document_file_integrity

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
OCR_SUMMARY_NOISE_PATTERN = re.compile(
    r'^(page\s+\d+(?:\s+of\s+\d+)?|confidential|draft|scan(?:ned)?\s+copy)$',
    re.IGNORECASE,
)
OCR_SUMMARY_METADATA_PATTERN = re.compile(
    r'\b(invoice date|date|total|subtotal|tax|balance|amount due|reference|ref\.?|page)\b',
    re.IGNORECASE,
)
OCR_SUMMARY_VALUE_PATTERN = re.compile(
    r'\b(total|subtotal|tax|balance|amount due)\b',
    re.IGNORECASE,
)
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


class DuplicateDocumentUpload(Exception):
    def __init__(self, duplicate, file_hash):
        self.duplicate = duplicate
        self.file_hash = file_hash
        super().__init__(f'This file was already uploaded as "{duplicate.title}".')


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


def queue_document_processing(document, *, user=None, request=None):
    from .tasks import process_document_task

    try:
        async_result = process_document_task.apply_async(args=[str(document.pk)], retry=False)
        log_document_audit(
            AuditAction.OCR_QUEUE,
            document=document,
            user=user,
            request=request,
            message='Document processing queued.',
            metadata={'task_id': async_result.id, 'backend': 'celery'},
        )
        return {'queued': True, 'fallback': False, 'task_id': async_result.id}
    except (CeleryError, KombuOperationalError, RedisError, OSError) as exc:
        logger.warning('Celery broker unavailable; processing document inline: %s', exc)
        log_document_audit(
            AuditAction.OCR_QUEUE,
            document=document,
            user=user,
            request=request,
            message='Redis unavailable; processing document inline.',
            metadata={'fallback': True, 'error': str(exc)[:255]},
        )
        result = process_document_pipeline(str(document.pk), fallback=True)
        return {'queued': False, 'fallback': True, 'result': result}


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
        convert_kwargs = {
            'dpi': 250,
            'first_page': 1,
            'last_page': max(1, int(getattr(settings, 'OCR_PDF_MAX_PAGES', 2))),
        }
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


def _mark_document_processing(document):
    now = timezone.now()
    Document.objects.filter(pk=document.pk).update(
        processing_status=DocumentProcessingStatus.PROCESSING,
        ocr_status=OCRStatus.PROCESSING,
        ocr_error='',
        updated_at=now,
    )
    document.processing_status = DocumentProcessingStatus.PROCESSING
    document.ocr_status = OCRStatus.PROCESSING
    document.ocr_error = ''
    document.updated_at = now


def _mark_document_failed(document, message, *, clear_file=False, ocr_status=OCRStatus.FAILED):
    update_fields = {
        'processing_status': DocumentProcessingStatus.FAILED,
        'ocr_status': ocr_status,
        'ocr_error': message[:255],
        'updated_at': timezone.now(),
    }
    if clear_file:
        update_fields['file'] = ''
    Document.objects.filter(pk=document.pk).update(**update_fields)
    document.processing_status = DocumentProcessingStatus.FAILED
    document.ocr_status = ocr_status
    document.ocr_error = message[:255]
    if clear_file:
        document.file.name = ''


def _mark_document_ready(document):
    Document.objects.filter(pk=document.pk).update(
        processing_status=DocumentProcessingStatus.READY,
        updated_at=timezone.now(),
    )
    document.processing_status = DocumentProcessingStatus.READY


def _apply_background_file_hash(document):
    metadata = stored_file_metadata(document)
    duplicate = (
        Document.objects
        .filter(file_hash=metadata.file_hash)
        .exclude(pk=document.pk)
        .first()
    )
    if duplicate:
        raise DuplicateDocumentUpload(duplicate, metadata.file_hash)

    document.original_filename = document.original_filename or metadata.original_filename
    document.file_size = metadata.file_size
    document.file_hash = metadata.file_hash

    try:
        document.save(update_fields=['original_filename', 'file_size', 'file_hash', 'updated_at'])
    except IntegrityError as exc:
        duplicate = (
            Document.objects
            .filter(file_hash=metadata.file_hash)
            .exclude(pk=document.pk)
            .first()
        )
        if duplicate:
            raise DuplicateDocumentUpload(duplicate, metadata.file_hash) from exc
        raise

    return metadata


def process_document_pipeline(document_id, *, fallback=False):
    document = Document.objects.get(pk=document_id)
    _mark_document_processing(document)

    try:
        metadata = _apply_background_file_hash(document)
        validate_document_file_integrity(document)
        ocr_status = process_document_ocr(document)
        suggestions = {}
        if ocr_status == OCRStatus.COMPLETED:
            suggestions = apply_ocr_metadata_suggestions(document)
        document.refresh_from_db()
        relocate_document_file(document)
        validate_document_file_integrity(document)

        if ocr_status == OCRStatus.COMPLETED:
            _mark_document_ready(document)
            message = 'Document processing completed.'
            final_status = DocumentProcessingStatus.READY
        else:
            final_status = DocumentProcessingStatus.FAILED
            message = document.ocr_error or 'Document processing failed.'
            _mark_document_failed(document, message, ocr_status=ocr_status)

        log_document_audit(
            AuditAction.OCR_PROCESS,
            document=document,
            message=message,
            metadata={
                'backend': 'inline' if fallback else 'celery',
                'processing_status': final_status,
                'ocr_status': ocr_status,
                'file_hash': metadata.file_hash,
                'file_size': metadata.file_size,
                'document_type': document.document_type,
                'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
                'metadata_source': document.metadata_source,
                'metadata_model': document.metadata_model,
                'headings': document.headings,
                'suggested_tags': suggestions.get('tags', []),
            },
        )
        return {'status': final_status, 'ocr_status': ocr_status}
    except DuplicateDocumentUpload as exc:
        logger.warning('Duplicate upload rejected for document %s: %s', document.pk, exc)
        if document.file:
            document.file.delete(save=False)
        _mark_document_failed(document, str(exc), clear_file=True)
        log_document_audit(
            AuditAction.OCR_PROCESS,
            document=document,
            message='Duplicate upload rejected.',
            metadata={
                'backend': 'inline' if fallback else 'celery',
                'processing_status': DocumentProcessingStatus.FAILED,
                'duplicate_document_id': str(exc.duplicate.pk),
                'file_hash': exc.file_hash,
            },
        )
        return {'status': DocumentProcessingStatus.FAILED, 'error': str(exc)}
    except (ValidationError, OSError, IntegrityError) as exc:
        message = _validation_message(exc)
        logger.warning('Document processing failed for %s: %s', document.pk, message)
        _mark_document_failed(document, message)
        log_document_audit(
            AuditAction.OCR_PROCESS,
            document=document,
            message='Document processing failed.',
            metadata={
                'backend': 'inline' if fallback else 'celery',
                'processing_status': DocumentProcessingStatus.FAILED,
                'error': message,
            },
        )
        return {'status': DocumentProcessingStatus.FAILED, 'error': message}
    except Exception as exc:
        logger.exception('Unexpected document processing failure for %s', document.pk)
        message = str(exc) or 'Unexpected document processing error.'
        _mark_document_failed(document, message)
        log_document_audit(
            AuditAction.OCR_PROCESS,
            document=document,
            message='Document processing failed unexpectedly.',
            metadata={
                'backend': 'inline' if fallback else 'celery',
                'processing_status': DocumentProcessingStatus.FAILED,
                'error': message[:255],
            },
        )
        return {'status': DocumentProcessingStatus.FAILED, 'error': message[:255]}


def _validation_message(exc):
    if hasattr(exc, 'messages'):
        return ' '.join(exc.messages)
    return str(exc) or 'Document processing failed.'


def suggested_title_from_ocr(ocr_text, fallback):
    normalized_lines = _normalized_ocr_lines(ocr_text)
    for index, line in enumerate(normalized_lines):
        if _ocr_summary_line_score(line, index) >= 8:
            return line[:255]
    for _, _, line in _ranked_ocr_summary_lines(ocr_text):
        return line[:255]
    for line in normalized_lines:
        if len(line) >= 4:
            return line[:255]
    return fallback[:255] or 'Scanned document'


def suggested_description_from_ocr(ocr_text, *, title=''):
    title_key = title.strip().lower()
    candidates = []
    for _, _, line in _ranked_ocr_summary_lines(ocr_text):
        if title_key and line.lower() == title_key:
            continue
        candidates.append(line)
        if len(candidates) == 2:
            break

    if candidates:
        summary = '. '.join(_trim_summary_line(line) for line in candidates if line)
        if summary:
            return summary[:600]

    fallback_lines = [
        line for line in _normalized_ocr_lines(ocr_text)
        if line and line.lower() != title_key
    ]
    if not fallback_lines:
        return ''

    fallback_text = ' '.join(fallback_lines)
    fallback_text = re.sub(r'\s+', ' ', fallback_text).strip()
    return fallback_text[:280]


def suggested_headings_from_ocr(ocr_text, *, title='', limit=5):
    title_key = title.strip().lower()
    headings = []
    for _, _, line in _ranked_ocr_summary_lines(ocr_text):
        if title_key and line.lower() == title_key:
            continue
        headings.append(line[:255])
        if len(headings) == limit:
            return headings

    for line in _normalized_ocr_lines(ocr_text):
        if title_key and line.lower() == title_key:
            continue
        headings.append(line[:255])
        if len(headings) == limit:
            break

    return headings


def _normalized_ocr_lines(ocr_text):
    seen = set()
    lines = []
    for raw_line in ocr_text.splitlines():
        cleaned = _trim_summary_line(raw_line)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(cleaned)
    return lines


def _trim_summary_line(line):
    return re.sub(r'\s+', ' ', line).strip(" \t-_:|")


def _ranked_ocr_summary_lines(ocr_text):
    ranked = []
    for index, line in enumerate(_normalized_ocr_lines(ocr_text)):
        score = _ocr_summary_line_score(line, index)
        if score >= 4:
            ranked.append((score, index, line))
    return sorted(ranked, key=lambda item: (-item[0], item[1], len(item[2])))


def _ocr_summary_line_score(line, index):
    lowered = line.lower()
    if OCR_SUMMARY_NOISE_PATTERN.match(lowered):
        return -100

    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&'/.-]*", line)
    if len(words) < 2:
        return -100

    alpha_chars = sum(character.isalpha() for character in line)
    digit_chars = sum(character.isdigit() for character in line)
    if alpha_chars < 4 or len(line) > 140:
        return -100

    score = max(0, 10 - index)

    if 2 <= len(words) <= 10:
        score += 6
    elif len(words) <= 14:
        score += 3
    else:
        score -= 3

    if 6 <= len(line) <= 80:
        score += 4
    elif len(line) <= 110:
        score += 1
    else:
        score -= 4

    if len(words) <= 4 and not re.search(r'[.!?]$', line):
        score += 2

    if line == line.upper() and alpha_chars >= 6:
        score += 3

    capitalized_words = sum(1 for word in words if word[:1].isupper())
    if capitalized_words / max(len(words), 1) >= 0.6:
        score += 2

    if any(pattern.search(line) for pattern in DATE_PATTERNS):
        score -= 10

    if digit_chars and digit_chars > alpha_chars / 2:
        score -= 4

    if ':' in line:
        before_colon, _, after_colon = line.partition(':')
        after_alpha = sum(character.isalpha() for character in after_colon)
        after_digits = sum(character.isdigit() for character in after_colon)
        if after_alpha >= 4 and after_alpha >= after_digits:
            score += 1
        else:
            score -= 3

    if OCR_SUMMARY_METADATA_PATTERN.search(lowered):
        score -= 4
        if digit_chars:
            score -= 6
    if OCR_SUMMARY_VALUE_PATTERN.search(lowered):
        score -= 6

    if re.search(r'[.!?]$', line):
        score -= 2

    if re.search(r'\b(report|invoice|contract|policy|memo|summary|minutes|register|statement)\b', lowered):
        score += 2

    return score


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
            document.headings = []
            document.metadata_source = MetadataSource.HEURISTIC
            document.metadata_model = ''
            document.save(update_fields=['title', 'headings', 'metadata_source', 'metadata_model', 'updated_at'])
        return {
            'title': document.title,
            'description': document.description,
            'headings': document.headings,
            'document_type': document.document_type,
            'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
            'tags': [],
            'metadata_source': document.metadata_source,
            'metadata_model': document.metadata_model,
        }

    heuristic_title = suggested_title_from_ocr(ocr_text, fallback_title)
    heuristic_description = suggested_description_from_ocr(ocr_text, title=heuristic_title)
    heuristic_headings = suggested_headings_from_ocr(ocr_text, title=heuristic_title)
    heuristic_document_type = suggested_document_type_from_ocr(ocr_text, document.document_type)
    heuristic_extracted_date = suggested_extracted_date_from_ocr(ocr_text)
    heuristic_tags = suggested_tags_from_ocr(ocr_text)

    ai_metadata = None
    try:
        ai_metadata = extract_document_metadata_with_ai(document)
    except Exception as exc:
        logger.warning('AI metadata extraction failed for %s: %s', document.pk, exc)

    title = _bounded_value(
        ai_metadata.get('title') if ai_metadata else '',
        fallback=heuristic_title,
        limit=255,
    )
    description = _bounded_value(
        ai_metadata.get('description') if ai_metadata else '',
        fallback=suggested_description_from_ocr(ocr_text, title=title),
        limit=600,
    )
    headings = _resolved_headings(
        ai_metadata.get('headings') if ai_metadata else [],
        fallback=suggested_headings_from_ocr(ocr_text, title=title),
        title=title,
    )
    document_type = _resolved_document_type(
        ai_metadata.get('document_type') if ai_metadata else '',
        fallback=heuristic_document_type,
    )
    extracted_date = _resolved_extracted_date(
        ai_metadata.get('extracted_date') if ai_metadata else '',
        fallback=heuristic_extracted_date,
    )
    tag_names = (ai_metadata.get('tags') if ai_metadata else []) or heuristic_tags

    document.title = title
    document.description = description
    document.headings = headings
    document.document_type = document_type
    document.extracted_date = extracted_date
    document.metadata_source = MetadataSource.AI if ai_metadata else MetadataSource.HEURISTIC
    document.metadata_model = (ai_metadata or {}).get('metadata_model', '')
    document.save(
        update_fields=[
            'title',
            'description',
            'headings',
            'document_type',
            'extracted_date',
            'metadata_source',
            'metadata_model',
            'updated_at',
        ]
    )

    if tag_names:
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]
        document.tags.set(tags)

    return {
        'title': document.title,
        'description': document.description,
        'headings': document.headings,
        'document_type': document.document_type,
        'extracted_date': document.extracted_date.isoformat() if document.extracted_date else '',
        'tags': tag_names,
        'metadata_source': document.metadata_source,
        'metadata_model': document.metadata_model,
    }


def _bounded_value(value, *, fallback='', limit=255):
    cleaned = ' '.join((value or '').split()).strip()
    if cleaned:
        return cleaned[:limit]
    return (fallback or '')[:limit]


def _resolved_headings(values, *, fallback=None, title='', limit=5):
    resolved = []
    seen = set()
    title_key = title.strip().lower()
    sources = values or fallback or []

    for source in sources:
        cleaned = ' '.join(str(source).split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key == title_key or key in seen:
            continue
        seen.add(key)
        resolved.append(cleaned[:255])
        if len(resolved) == limit:
            return resolved

    return resolved


def _resolved_document_type(value, *, fallback='other'):
    valid_values = dict(DOC_TYPE_CHOICES)
    if value in valid_values:
        return value
    return fallback if fallback in valid_values else 'other'


def _resolved_extracted_date(value, *, fallback=None):
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback


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
            processing_status=DocumentProcessingStatus.PROCESSING,
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
    Document.objects.filter(pk=document.pk).update(
        processing_status=(
            DocumentProcessingStatus.READY
            if job.status == OCRJobStatus.COMPLETED
            else DocumentProcessingStatus.FAILED
        ),
        updated_at=timezone.now(),
    )
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
