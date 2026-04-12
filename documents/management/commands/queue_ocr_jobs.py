from django.core.management.base import BaseCommand

from documents.models import Document, OCRStatus
from documents.services import enqueue_document_ocr


class Command(BaseCommand):
    help = 'Queue OCR jobs for documents that need background processing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--document-id',
            action='append',
            dest='document_ids',
            help='Specific document UUID to queue. Repeat the flag to target multiple documents.',
        )
        parser.add_argument(
            '--status',
            action='append',
            choices=[choice for choice, _ in OCRStatus.choices],
            help='Filter by document OCR status. Repeat the flag to include multiple statuses.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Maximum number of matching documents to queue.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Create a new OCR job even when a queued or processing job already exists.',
        )

    def handle(self, *args, **options):
        queryset = Document.objects.order_by('created_at')
        document_ids = options.get('document_ids') or []
        selected_statuses = options.get('status') or []
        limit = options.get('limit')

        if document_ids:
            queryset = queryset.filter(pk__in=document_ids)

        if selected_statuses:
            queryset = queryset.filter(ocr_status__in=selected_statuses)
        else:
            queryset = queryset.filter(
                ocr_status__in=[
                    OCRStatus.PENDING,
                    OCRStatus.PROCESSING,
                    OCRStatus.FAILED,
                    OCRStatus.SKIPPED,
                ]
            )

        if limit:
            queryset = queryset[:limit]

        queued = 0
        skipped = 0
        for document in queryset:
            _, created = enqueue_document_ocr(document, force=options['force'])
            if created:
                queued += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f'Queued {queued} OCR job(s). skipped_active={skipped}')
        )
