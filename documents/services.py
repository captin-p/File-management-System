import logging
import os
from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'which', 'document',
    'invoice', 'report', 'contract', 'page', 'pages', 'company', 'department',
    'unit', 'management', 'policy', 'statement', 'information', 'account',
}


def extract_text_from_image(file_path):
    try:
        with Image.open(file_path) as image:
            return pytesseract.image_to_string(image, lang='eng').strip()
    except Exception as exc:
        logger.exception('OCR image extraction failed: %s', exc)
        return ''


def extract_text_from_pdf(file_path):
    text = []
    try:
        images = convert_from_path(file_path, dpi=250)
        for page_image in images:
            text.append(pytesseract.image_to_string(page_image, lang='eng'))
    except Exception as exc:
        logger.exception('OCR PDF extraction failed: %s', exc)
    return '\n'.join(text).strip()


def extract_text_from_file(file_path):
    extension = Path(file_path).suffix.lower()
    if extension in {'.pdf'}:
        return extract_text_from_pdf(file_path)
    if extension in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}:
        return extract_text_from_image(file_path)
    return ''


def suggest_title_from_text(text):
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return lines[0][:200]
    return text.strip()[:200]


def suggest_tags_from_text(text, limit=6):
    if not text:
        return []
    words = [word.strip('.,:;()[]') for word in text.lower().split() if len(word) > 3]
    candidates = [word for word in words if word not in STOPWORDS and word.isalpha()]
    frequency = {}
    for word in candidates:
        frequency[word] = frequency.get(word, 0) + 1
    sorted_tags = sorted(frequency.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in sorted_tags[:limit]]


def run_document_ocr(document):
    file_path = document.file.path
    if not os.path.exists(file_path):
        logger.warning('Document file not found for OCR: %s', file_path)
        return

    extracted = extract_text_from_file(file_path)
    document.ocr_text = extracted
    if not document.title:
        document.title = suggest_title_from_text(extracted)
    document.save()
    return suggest_tags_from_text(extracted)
