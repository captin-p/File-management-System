from django.contrib import admin
from .models import Document

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'unit', 'ocr_status', 'uploaded_by', 'created_at', 'updated_at')
    list_filter = ('department', 'unit', 'ocr_status', 'created_at', 'updated_at')
    search_fields = ('title', 'description', 'ocr_text', 'uploaded_by__username', 'department__name', 'unit__name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'ocr_status', 'ocr_error')
