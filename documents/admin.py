from django.contrib import admin
from .models import Document, OCRJob, Tag

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ('name',)

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'document_type', 'department', 'unit', 'ocr_status', 'uploaded_by', 'created_at', 'updated_at')
    list_filter = ('document_type', 'department', 'unit', 'ocr_status', 'created_at', 'updated_at')
    search_fields = ('title', 'description', 'ocr_text', 'uploaded_by__username', 'department__name', 'unit__name', 'tags__name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'ocr_status', 'ocr_error')
    filter_horizontal = ('tags',)

@admin.register(OCRJob)
class OCRJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'status', 'attempts', 'created_at', 'started_at', 'finished_at')
    list_filter = ('status', 'created_at', 'started_at', 'finished_at')
    search_fields = ('document__title', 'document__id', 'error')
    readonly_fields = ('id', 'created_at', 'updated_at', 'started_at', 'finished_at')
