import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchVector
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from .forms import DocumentUploadForm, DocumentMetadataForm, DocumentSearchForm
from .models import Document, Tag
from .services import run_document_ocr, suggest_tags_from_text

logger = logging.getLogger(__name__)

def user_can_access_document(user, document):
    if user.is_superuser or user.is_admin():
        return True
    if not document.department:
        return False
    if user.department and document.department == user.department:
        return True
    return False

@login_required
def dashboard(request):
    documents = Document.objects.all()
    if not request.user.is_superuser and not request.user.is_admin():
        documents = documents.filter(department=request.user.department)
    context = {
        'document_count': documents.count(),
        'recent_documents': documents.order_by('-upload_date')[:5],
        'departments': {doc.department.name for doc in documents if doc.department},
    }
    return render(request, 'dashboard.html', context)

@login_required
def upload_document(request):
    if request.method == 'POST':
        upload_form = DocumentUploadForm(request.POST, request.FILES)
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
        upload_form = DocumentUploadForm(initial={
            'department': request.user.department,
            'unit': request.user.unit,
        })
    return render(request, 'documents/upload.html', {'form': upload_form})

@login_required
def edit_document(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not request.user.is_superuser and not user_can_access_document(request.user, document):
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
    query = Document.objects.all()
    if not request.user.is_superuser and not request.user.is_admin():
        query = query.filter(department=request.user.department)

    search_form = DocumentSearchForm(request.GET or None)
    if search_form.is_valid():
        data = search_form.cleaned_data
        if data.get('query'):
            query = query.annotate(search=SearchVector('title', 'description', 'ocr_text')).filter(search=data['query'])
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
    documents = query.order_by('-upload_date')[:100]
    return render(request, 'documents/document_list.html', {'documents': documents, 'form': search_form})

@login_required
def document_detail(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not request.user.is_superuser and not user_can_access_document(request.user, document):
        raise Http404('Document not found')
    return render(request, 'documents/document_detail.html', {'document': document})

@login_required
def delete_document(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not request.user.is_superuser and not user_can_access_document(request.user, document):
        raise Http404('Document not found')
    if request.method == 'POST':
        document.file.delete(save=False)
        document.delete()
        messages.success(request, 'Document deleted successfully.')
        return redirect('documents:document_list')
    return render(request, 'documents/document_delete.html', {'document': document})

@login_required
def document_api_list(request):
    documents = Document.objects.all()
    if not request.user.is_superuser and not request.user.is_admin():
        documents = documents.filter(department=request.user.department)
    payload = [
        {
            'id': document.pk,
            'title': document.title,
            'department': document.department.name if document.department else None,
            'unit': document.unit.name if document.unit else None,
            'document_type': document.document_type,
            'upload_date': document.upload_date.isoformat(),
        }
        for document in documents[:50]
    ]
    return JsonResponse({'documents': payload})

@login_required
def document_api_detail(request, pk):
    document = get_object_or_404(Document, pk=pk)
    if not request.user.is_superuser and not user_can_access_document(request.user, document):
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
