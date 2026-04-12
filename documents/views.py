import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib.postgres.search import SearchVector
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from .forms import DocumentUploadForm, DocumentMetadataForm, DocumentSearchForm
from .models import Document
from .services import run_document_ocr
from .utils import (
    filter_documents_for_user,
    user_can_view_document,
    user_can_edit_document,
    user_can_delete_document,
)

logger = logging.getLogger(__name__)

@login_required
def dashboard(request):
    documents = filter_documents_for_user(request.user)
    context = {
        'document_count': documents.count(),
        'recent_documents': documents.order_by('-upload_date')[:5],
        'departments': {doc.department.name for doc in documents if doc.department},
    }
    return render(request, 'dashboard.html', context)

@login_required
def upload_document(request):
    if request.method == 'POST':
        upload_form = DocumentUploadForm(request.POST, request.FILES, user=request.user)
        if upload_form.is_valid():
            document = upload_form.save(commit=False)
            document.created_by = request.user
            document.save()
            upload_form.save_m2m()
            suggested_tags = run_document_ocr(document)
            if suggested_tags:
                messages.info(request, 'OCR completed and metadata was pre-filled.')
                request.session['suggested_tags'] = suggested_tags
            return redirect('documents:edit_document', pk=document.pk)
    else:
        upload_form = DocumentUploadForm(
            initial={
                'department': request.user.department,
                'unit': request.user.unit,
            },
            user=request.user,
        )
    return render(request, 'documents/upload.html', {'form': upload_form})

@login_required
def edit_document(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not user_can_edit_document(request.user, document):
        raise Http404('Document not found')

    suggested = []
    if request.method == 'POST':
        form = DocumentMetadataForm(request.POST, instance=document)
        if form.is_valid():
            form.save()
            messages.success(request, 'Document metadata saved successfully.')
            return redirect('documents:document_detail', pk=document.pk)
    else:
        tags_text = ', '.join(tag.name for tag in document.tags.all())
        form = DocumentMetadataForm(instance=document, initial={'tags': tags_text})
        suggested = request.session.pop('suggested_tags', [])
    return render(request, 'documents/document_edit.html', {'form': form, 'document': document, 'suggested_tags': suggested})

@login_required
def document_list(request):
    query = filter_documents_for_user(request.user)
    search_form = DocumentSearchForm(request.GET or None)
    if search_form.is_valid():
        data = search_form.cleaned_data
        if data.get('query'):
            if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
                query = query.annotate(search=SearchVector('title', 'description', 'ocr_text')).filter(search=data['query'])
            else:
                query = query.filter(
                    Q(title__icontains=data['query']) |
                    Q(description__icontains=data['query']) |
                    Q(ocr_text__icontains=data['query'])
                )
        if data.get('tags'):
            for tag in [tag.strip().lower() for tag in data['tags'].split(',') if tag.strip()]:
                query = query.filter(tags__name__icontains=tag)
        if data.get('department'):
            query = query.filter(department=data['department'])
        if data.get('unit'):
            query = query.filter(unit=data['unit'])
        if data.get('document_type'):
            query = query.filter(document_type=data['document_type'])
        if data.get('date_from'):
            query = query.filter(upload_date__gte=data['date_from'])
        if data.get('date_to'):
            query = query.filter(upload_date__lte=data['date_to'])
    documents = query.order_by('-upload_date').distinct()[:100]
    return render(request, 'documents/document_list.html', {'documents': documents, 'form': search_form})

@login_required
def browse_documents(request, department_slug=None, year=None, document_type=None):
    documents = filter_documents_for_user(request.user)
    department_rows = (
        documents
        .filter(department__isnull=False)
        .values('department__name', 'department__slug')
        .distinct()
        .order_by('department__name')
    )
    departments = [{'name': row['department__name'], 'slug': row['department__slug']} for row in department_rows]
    selected_department = None
    selected_year = year
    selected_type = document_type
    years = []
    types = []
    folder_docs = None

    if department_slug:
        documents = documents.filter(department__slug=department_slug)
        selected_row = documents.values('department__name', 'department__slug').first()
        selected_department = {'name': selected_row['department__name'], 'slug': selected_row['department__slug']} if selected_row else None
        years = [date.year for date in documents.dates('upload_date', 'year', order='DESC')]

    if selected_department and year:
        documents = documents.filter(upload_date__year=year)
        types = sorted(set(documents.values_list('document_type', flat=True)))

    if selected_department and year and document_type:
        documents = documents.filter(document_type=document_type)
        folder_docs = documents.order_by('-upload_date')

    return render(request, 'documents/browse.html', {
        'departments': departments,
        'selected_department': selected_department,
        'years': years,
        'types': types,
        'selected_year': selected_year,
        'selected_type': selected_type,
        'folder_docs': folder_docs,
    })

@login_required
def document_detail(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not user_can_view_document(request.user, document):
        raise Http404('Document not found')
    return render(request, 'documents/document_detail.html', {'document': document})

@login_required
def delete_document(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not user_can_delete_document(request.user, document):
        raise Http404('Document not found')
    if request.method == 'POST':
        document.file.delete(save=False)
        document.delete()
        messages.success(request, 'Document deleted successfully.')
        return redirect('documents:document_list')
    return render(request, 'documents/document_delete.html', {'document': document})

@login_required
def document_api_list(request):
    documents = filter_documents_for_user(request.user)
    search = request.GET.get('q')
    tags = request.GET.get('tags')
    department = request.GET.get('department')
    unit = request.GET.get('unit')
    document_type = request.GET.get('document_type')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if search:
        if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
            documents = documents.annotate(search_vector=SearchVector('title', 'description', 'ocr_text')).filter(search_vector=search)
        else:
            documents = documents.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(ocr_text__icontains=search)
            )
    if tags:
        for tag in [tag.strip().lower() for tag in tags.split(',') if tag.strip()]:
            documents = documents.filter(tags__name__icontains=tag)
    if department:
        documents = documents.filter(department__slug=department)
    if unit:
        documents = documents.filter(unit__slug=unit)
    if document_type:
        documents = documents.filter(document_type=document_type)
    if date_from:
        documents = documents.filter(upload_date__gte=date_from)
    if date_to:
        documents = documents.filter(upload_date__lte=date_to)

    payload = [
        {
            'id': document.pk,
            'title': document.title,
            'department': document.department.name if document.department else None,
            'unit': document.unit.name if document.unit else None,
            'document_type': document.document_type,
            'upload_date': document.upload_date.isoformat(),
            'file_url': request.build_absolute_uri(document.file.url) if document.file else None,
        }
        for document in documents.distinct()[:50]
    ]
    return JsonResponse({'documents': payload})

@login_required
def document_api_detail(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not user_can_view_document(request.user, document):
        raise Http404('Document not found')
    payload = {
        'id': document.pk,
        'title': document.title,
        'description': document.description,
        'department': document.department.name if document.department else None,
        'unit': document.unit.name if document.unit else None,
        'tags': [tag.name for tag in document.tags.all()],
        'document_type': document.document_type,
        'upload_date': document.upload_date.isoformat(),
        'ocr_text': document.ocr_text,
    }
    return JsonResponse(payload)
