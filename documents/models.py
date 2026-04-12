import os
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models import F, Q
from django.utils.text import get_valid_filename

from accounts.models import Department, Unit

from .validators import validate_document_file


DOC_TYPE_CHOICES = [
    ('invoice', 'Invoice'),
    ('report', 'Report'),
    ('contract', 'Contract'),
    ('policy', 'Policy'),
    ('memo', 'Memo'),
    ('other', 'Other'),
]

SEARCH_CONFIG = 'simple'


def document_search_vector():
    return (
        SearchVector('title', weight='A', config=SEARCH_CONFIG) +
        SearchVector('description', weight='B', config=SEARCH_CONFIG) +
        SearchVector('ocr_text', weight='C', config=SEARCH_CONFIG)
    )


class OCRStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    SKIPPED = 'skipped', 'Skipped'


class OCRJobStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class AuditAction(models.TextChoices):
    UPLOAD = 'upload', 'Upload'
    VIEW = 'view', 'View'
    EDIT = 'edit', 'Edit'
    DELETE = 'delete', 'Delete'
    OCR_QUEUE = 'ocr_queue', 'Queue OCR'
    OCR_PROCESS = 'ocr_process', 'Process OCR'


def document_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    safe_name = get_valid_filename(Path(filename).stem)[:80]
    generated_name = f"{safe_name or 'document'}-{uuid.uuid4().hex}{extension}"
    return os.path.join('documents', generated_name)


class DocumentQuerySet(models.QuerySet):
    def for_list(self):
        return self.select_related('uploaded_by', 'department', 'unit').prefetch_related('tags').only(
            'id',
            'title',
            'description',
            'file',
            'created_at',
            'updated_at',
            'ocr_status',
            'document_type',
            'department__name',
            'unit__name',
            'department_id',
            'unit_id',
            'uploaded_by__username',
            'uploaded_by__first_name',
            'uploaded_by__last_name',
        )

    def search(self, term):
        if not term:
            return self

        cleaned_term = term.strip()
        if not cleaned_term:
            return self

        if connection.vendor == 'postgresql':
            query = SearchQuery(cleaned_term, config=SEARCH_CONFIG, search_type='websearch')
            return (
                self.annotate(rank=SearchRank(F('search_vector'), query))
                .filter(search_vector=query)
                .order_by('-rank', '-created_at')
            )

        return self.filter(
            Q(title__icontains=cleaned_term) |
            Q(description__icontains=cleaned_term) |
            Q(ocr_text__icontains=cleaned_term)
        ).order_by('-created_at')


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Tag'
        verbose_name_plural = 'Tags'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.strip().lower()
        super().save(*args, **kwargs)


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to=document_upload_path, validators=[validate_document_file])
    document_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, default='other')
    ocr_text = models.TextField(blank=True)
    search_vector = SearchVectorField(null=True, editable=False)
    ocr_status = models.CharField(
        max_length=20,
        choices=OCRStatus.choices,
        default=OCRStatus.PENDING,
    )
    ocr_error = models.CharField(max_length=255, blank=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='documents',
        null=True,
        blank=True,
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        related_name='documents',
        null=True,
        blank=True,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='documents',
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['title']),
            models.Index(fields=['created_at']),
            models.Index(fields=['uploaded_by', 'created_at']),
            models.Index(fields=['department', 'created_at']),
            models.Index(fields=['unit', 'created_at']),
            models.Index(fields=['ocr_status', 'created_at']),
            models.Index(fields=['document_type', 'created_at']),
        ]
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'

    def __str__(self):
        return self.title

    def clean(self):
        if self.unit_id and self.department_id and self.unit.department_id != self.department_id:
            raise ValidationError({'unit': 'Selected unit must belong to the selected department.'})


class OCRJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='ocr_jobs')
    status = models.CharField(
        max_length=20,
        choices=OCRJobStatus.choices,
        default=OCRJobStatus.QUEUED,
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['document', 'status', 'created_at']),
        ]
        verbose_name = 'OCR job'
        verbose_name_plural = 'OCR jobs'

    def __str__(self):
        return f'{self.document_id} / {self.status}'


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='document_audit_logs',
    )
    action = models.CharField(max_length=30, choices=AuditAction.choices)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['document', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
        ]
        verbose_name = 'Audit log'
        verbose_name_plural = 'Audit logs'

    def __str__(self):
        actor = self.actor or 'system'
        return f'{self.get_action_display()} / {actor} / {self.created_at:%Y-%m-%d %H:%M}'
