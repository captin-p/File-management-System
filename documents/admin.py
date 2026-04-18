from django.contrib import admin
from .models import AuditLog, Document, OCRJob, Tag

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ('name',)

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'processing_status', 'document_type', 'metadata_source', 'extracted_date', 'department', 'unit', 'ocr_status', 'original_filename', 'uploaded_by', 'created_at', 'updated_at')
    list_filter = ('processing_status', 'document_type', 'metadata_source', 'department', 'unit', 'ocr_status', 'extracted_date', 'created_at', 'updated_at')
    search_fields = ('title', 'description', 'ocr_text', 'original_filename', 'file_hash', 'uploaded_by__username', 'department__name', 'unit__name', 'tags__name')
    readonly_fields = ('id', 'original_filename', 'file_size', 'file_hash', 'created_at', 'updated_at', 'ocr_status', 'ocr_error', 'metadata_source', 'metadata_model', 'headings')
    filter_horizontal = ('tags',)

@admin.register(OCRJob)
class OCRJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'status', 'attempts', 'created_at', 'started_at', 'finished_at')
    list_filter = ('status', 'created_at', 'started_at', 'finished_at')
    search_fields = ('document__title', 'document__id', 'error')
    readonly_fields = ('id', 'created_at', 'updated_at', 'started_at', 'finished_at')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'document', 'actor', 'ip_address', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('document__title', 'document__id', 'actor__username', 'message')
    readonly_fields = ('id', 'document', 'actor', 'action', 'message', 'metadata', 'ip_address', 'user_agent', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
