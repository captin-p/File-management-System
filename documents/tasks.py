from celery import shared_task

from .services import process_document_pipeline


@shared_task(bind=True, autoretry_for=(OSError,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def process_document_task(self, document_id):
    return process_document_pipeline(document_id)
