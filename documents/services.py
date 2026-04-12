import logging
import os
from pathlib import Path

from .models import OCRStatus

logger = logging.getLogger(__name__)


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
