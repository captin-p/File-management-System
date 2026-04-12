from django.core.management.base import BaseCommand

from documents.models import Document, OCRStatus
from documents.services import process_document_ocr


class Command(BaseCommand):
    help = 'Re-run OCR for documents that need extracted text.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--document-id',
            action='append',
            dest='document_ids',
            help='Specific document UUID to process. Repeat the flag to target multiple documents.',
        )
        parser.add_argument(
            '--status',
            action='append',
            choices=[choice for choice, _ in OCRStatus.choices],
            help='Filter by existing OCR status. Repeat the flag to include multiple statuses.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Maximum number of matching documents to process.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Include completed documents in the selected queryset.',
        )

    def handle(self, *args, **options):
        queryset = Document.objects.order_by('created_at')
        document_ids = options.get('document_ids') or []
        selected_statuses = options.get('status') or []
        force = options.get('force', False)
        limit = options.get('limit')

        if document_ids:
            queryset = queryset.filter(pk__in=document_ids)

        if selected_statuses:
            queryset = queryset.filter(ocr_status__in=selected_statuses)
        elif not force:
            queryset = queryset.filter(
                ocr_status__in=[OCRStatus.PENDING, OCRStatus.FAILED, OCRStatus.SKIPPED]
            )

        if limit:
            queryset = queryset[:limit]

        documents = list(queryset)
        if not documents:
            self.stdout.write('No matching documents to process.')
            return

        status_counts = {}
        for document in documents:
            new_status = process_document_ocr(document)
            status_counts[new_status] = status_counts.get(new_status, 0) + 1
            self.stdout.write(f'{document.pk}: {new_status}')

        summary = ', '.join(f'{status}={count}' for status, count in sorted(status_counts.items()))
        self.stdout.write(self.style.SUCCESS(f'Processed {len(documents)} document(s). {summary}'))
