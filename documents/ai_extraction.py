import base64
import json
import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings

from .models import DOC_TYPE_CHOICES
from .utils import normalize_tag_names

logger = logging.getLogger(__name__)

AI_METADATA_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'description': {'type': 'string'},
        'headings': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'document_type': {
            'type': 'string',
            'enum': [value for value, _ in DOC_TYPE_CHOICES],
        },
        'tags': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'extracted_date': {
            'type': ['string', 'null'],
        },
    },
    'required': ['title', 'description', 'headings', 'document_type', 'tags', 'extracted_date'],
    'additionalProperties': False,
}

SYSTEM_PROMPT = (
    'You extract document metadata from OCR text and, when available, a first-page image preview. '
    'Identify the document title, up to five heading-like lines, a short description, document type, '
    'tags, and a document date. Prefer visible headings, title text, section headers, and bold-like lines. '
    'Ignore scanner artifacts, page numbers, repeated headers, totals, hashes, filenames, and boilerplate unless '
    'they are clearly the main title. The description must be concise and should read like a heading-style summary, '
    'not a transcript. Return only data that fits the schema.'
)


def extract_document_metadata_with_ai(document):
    if not getattr(settings, 'AI_METADATA_ENABLED', False):
        return None
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        logger.info('AI metadata extraction skipped for %s: OPENAI_API_KEY is not configured.', document.pk)
        return None
    if not (document.ocr_text or '').strip():
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning('AI metadata extraction skipped for %s: openai package is not installed.', document.pk)
        return None

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    input_content = [
        {
            'type': 'input_text',
            'text': _metadata_input_text(document),
        }
    ]

    image_url = _document_preview_data_url(document)
    if image_url:
        input_content.append(
            {
                'type': 'input_image',
                'image_url': image_url,
            }
        )

    response = client.responses.create(
        model=settings.AI_METADATA_MODEL,
        store=False,
        max_output_tokens=700,
        input=[
            {
                'role': 'system',
                'content': [{'type': 'input_text', 'text': SYSTEM_PROMPT}],
            },
            {
                'role': 'user',
                'content': input_content,
            },
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': 'document_metadata',
                'strict': True,
                'schema': AI_METADATA_SCHEMA,
            }
        },
    )

    payload = json.loads(response.output_text)
    return {
        'title': (payload.get('title') or '').strip(),
        'description': (payload.get('description') or '').strip(),
        'headings': _normalized_headings(payload.get('headings') or []),
        'document_type': payload.get('document_type') or 'other',
        'tags': normalize_tag_names(payload.get('tags') or [])[:6],
        'extracted_date': (payload.get('extracted_date') or '').strip(),
        'metadata_source': 'ai',
        'metadata_model': getattr(response, 'model', '') or settings.AI_METADATA_MODEL,
    }


def _metadata_input_text(document):
    max_chars = max(1000, int(getattr(settings, 'AI_METADATA_MAX_OCR_CHARS', 12000)))
    ocr_text = (document.ocr_text or '').strip()
    if len(ocr_text) > max_chars:
        ocr_text = ocr_text[:max_chars].rsplit('\n', 1)[0].strip()

    return '\n'.join(
        [
            f'Original filename: {document.original_filename or Path(document.file.name).name}',
            f'Current title: {document.title}',
            f'Current document type: {document.document_type}',
            'OCR text:',
            ocr_text,
        ]
    )


def _normalized_headings(values):
    headings = []
    seen = set()
    for value in values:
        cleaned = ' '.join(str(value).split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        headings.append(cleaned[:255])
        if len(headings) == getattr(settings, 'AI_METADATA_MAX_HEADINGS', 5):
            break
    return headings


def _document_preview_data_url(document):
    if not getattr(settings, 'AI_METADATA_USE_IMAGE', True):
        return None
    if not document.file:
        return None

    try:
        extension = Path(document.file.name).suffix.lower()
        image = _document_preview_image(document.file.path, extension)
        if image is None:
            return None
        image.thumbnail((1600, 1600))
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=82)
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{encoded}'
    except Exception as exc:
        logger.warning('AI image preview unavailable for %s: %s', document.pk, exc)
        return None


def _document_preview_image(file_path, extension):
    from PIL import Image

    if extension == '.pdf':
        from pdf2image import convert_from_path

        convert_kwargs = {'first_page': 1, 'last_page': 1, 'dpi': 160}
        poppler_path = getattr(settings, 'OCR_POPPLER_PATH', '')
        if poppler_path:
            convert_kwargs['poppler_path'] = poppler_path
        pages = convert_from_path(file_path, **convert_kwargs)
        if not pages:
            return None
        return pages[0].convert('RGB')

    if extension in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}:
        with Image.open(file_path) as image:
            return image.convert('RGB')

    return None
