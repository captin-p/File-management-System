from itertools import islice
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from documents.models import Document, document_search_vector


def chunked(iterator, size):
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


class Command(BaseCommand):
    help = 'Refresh the materialized PostgreSQL search vector for document records.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of documents to update per database batch.',
        )
        parser.add_argument(
            '--document-id',
            action='append',
            dest='document_ids',
            help='Limit the rebuild to a specific document UUID. Repeat the flag for multiple documents.',
        )

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(
                self.style.WARNING(
                    'Search index rebuild skipped. SQLite/dev mode uses direct text filtering.'
                )
            )
            return

        batch_size = max(1, options['batch_size'])
        document_ids = self._validated_document_ids(options.get('document_ids') or [])
        queryset = Document.objects.order_by('pk')
        if document_ids:
            queryset = queryset.filter(pk__in=document_ids)

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.WARNING('No matching documents found.'))
            return

        processed = 0
        id_iterator = queryset.values_list('pk', flat=True).iterator(chunk_size=batch_size)
        for id_batch in chunked(id_iterator, batch_size):
            processed += Document.objects.filter(pk__in=id_batch).update(
                search_vector=document_search_vector()
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Rebuilt search vectors for {processed} of {total} document(s).'
            )
        )

    def _validated_document_ids(self, raw_ids):
        document_ids = []
        for raw_id in raw_ids:
            try:
                document_ids.append(UUID(str(raw_id)))
            except (TypeError, ValueError) as exc:
                raise CommandError(f'Invalid document UUID: {raw_id}') from exc
        return document_ids
