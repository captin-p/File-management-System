import os
from datetime import datetime
from django.conf import settings
from django.db import models
from django.utils.text import slugify
from accounts.models import Department, Unit

DOC_TYPE_CHOICES = [
    ('invoice', 'Invoice'),
    ('report', 'Report'),
    ('contract', 'Contract'),
    ('policy', 'Policy'),
    ('other', 'Other'),
]


def document_upload_path(instance, filename):
    department = instance.department.slug if instance.department else 'unassigned'
    year = datetime.utcnow().year
    document_type = slugify(instance.document_type or 'unclassified')
    return os.path.join('storage', department, str(year), document_type, filename)

class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)

    class Meta:
        verbose_name = 'Tag'
        verbose_name_plural = 'Tags'

    def __str__(self):
        return self.name

class Document(models.Model):
    file = models.FileField(upload_to=document_upload_path)
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents')
    tags = models.ManyToManyField(Tag, blank=True, related_name='documents')
    document_type = models.CharField(max_length=80, choices=DOC_TYPE_CHOICES, default='other')
    upload_date = models.DateTimeField(auto_now_add=True)
    ocr_text = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_documents')
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-upload_date']
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'

    def __str__(self):
        return self.title or os.path.basename(self.file.name)

    def save(self, *args, **kwargs):
        if not self.title and self.ocr_text:
            self.title = self.title or self.ocr_text.strip().split('\n')[0][:150]
        super().save(*args, **kwargs)
