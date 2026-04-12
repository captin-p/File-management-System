import os
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models import Q
from django.utils.text import get_valid_filename

from accounts.models import Department, Unit

from .validators import validate_document_file


class OCRStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    SKIPPED = 'skipped', 'Skipped'


def document_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    safe_name = get_valid_filename(Path(filename).stem)[:80]
    generated_name = f"{safe_name or 'document'}-{uuid.uuid4().hex}{extension}"
    return os.path.join('documents', generated_name)


class DocumentQuerySet(models.QuerySet):
    def for_list(self):
        return self.select_related('uploaded_by', 'department', 'unit').only(
            'id',
            'title',
            'description',
            'file',
            'created_at',
            'updated_at',
            'ocr_status',
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
            vector = (
                SearchVector('title', weight='A') +
                SearchVector('description', weight='B') +
                SearchVector('ocr_text', weight='C')
            )
            query = SearchQuery(cleaned_term)
            return (
                self.annotate(rank=SearchRank(vector, query))
                .filter(rank__gt=0)
                .order_by('-rank', '-created_at')
            )

        return self.filter(
            Q(title__icontains=cleaned_term) |
            Q(description__icontains=cleaned_term) |
            Q(ocr_text__icontains=cleaned_term)
        ).order_by('-created_at')


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to=document_upload_path, validators=[validate_document_file])
    ocr_text = models.TextField(blank=True)
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
        ]
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'

    def __str__(self):
        return self.title

    def clean(self):
        if self.unit_id and self.department_id and self.unit.department_id != self.department_id:
            raise ValidationError({'unit': 'Selected unit must belong to the selected department.'})
