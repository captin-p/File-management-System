import time
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from documents.models import OCRJobStatus
from documents.services import process_next_ocr_job


class Command(BaseCommand):
    help = 'Process queued OCR jobs in the background.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Maximum number of jobs to process before exiting. Ignored when --loop is used.',
        )
        parser.add_argument(
            '--loop',
            action='store_true',
            help='Keep polling for new OCR jobs until the process is stopped.',
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=5.0,
            help='Seconds to sleep between empty queue checks when --loop is used.',
        )
        parser.add_argument(
            '--document-id',
            help='Only process queued OCR jobs for this document UUID.',
        )

    def handle(self, *args, **options):
        document_id = self._validated_document_id(options.get('document_id'))
        limit = max(1, options['limit'])
        loop = options['loop']
        sleep_seconds = max(0.1, options['sleep'])

        processed = 0
        status_counts = {OCRJobStatus.COMPLETED: 0, OCRJobStatus.FAILED: 0}

        while True:
            job = process_next_ocr_job(document_id=document_id)
            if job is None:
                if loop:
                    time.sleep(sleep_seconds)
                    continue
                break

            processed += 1
            status_counts[job.status] = status_counts.get(job.status, 0) + 1
            if options['verbosity'] >= 2:
                self.stdout.write(f'{job.id}: {job.document_id} -> {job.status}')

            if not loop and processed >= limit:
                break

        summary = ', '.join(
            f'{status}={count}' for status, count in sorted(status_counts.items())
        )
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} OCR job(s). {summary}'))

    def _validated_document_id(self, raw_id):
        if not raw_id:
            return None
        try:
            return UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise CommandError(f'Invalid document UUID: {raw_id}') from exc
