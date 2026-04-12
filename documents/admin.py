from django.contrib import admin
from .models import Document, Tag

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ('name',)

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'unit', 'document_type', 'upload_date', 'created_by')
    list_filter = ('department', 'unit', 'document_type', 'upload_date')
    search_fields = ('title', 'description', 'ocr_text')
    raw_id_fields = ('created_by',)
    filter_horizontal = ('tags',)
