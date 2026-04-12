from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import DocumentMetadataForm, DocumentSearchForm, DocumentUploadForm
from .models import DOC_TYPE_CHOICES, Document, OCRStatus
from .services import apply_ocr_metadata_suggestions, process_document_ocr
from .utils import (
    apply_document_filters,
    deletable_documents_for_user,
    editable_documents_for_user,
    filter_documents_for_user,
    paginate_queryset,
    user_can_delete_document,
    user_can_edit_document,
    user_can_upload_documents,
)


class UserScopedFormMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class AccessibleDocumentMixin(LoginRequiredMixin):
    def get_queryset(self):
        return filter_documents_for_user(self.request.user, Document.objects.for_list())


class DocumentFilterMixin:
    search_form_class = DocumentSearchForm

    def build_search_form(self):
        return self.search_form_class(self.request.GET or None, user=self.request.user)

    def filter_document_queryset(self, queryset):
        self.search_form = self.build_search_form()
        if self.search_form.is_valid():
            data = self.search_form.cleaned_data
            queryset = apply_document_filters(
                queryset,
                query=data.get('q'),
                department=data.get('department'),
                unit=data.get('unit'),
                ocr_status=data.get('ocr_status'),
                document_type=data.get('document_type'),
                tags=data.get('tags'),
            )

        if not self.request.GET.get('q'):
            queryset = queryset.order_by('-created_at')

        return queryset

    def document_filter_context(self):
        if not getattr(self, 'search_form', None) or not self.search_form.is_valid():
            return {
                'search_query': '',
                'selected_department_id': '',
                'selected_unit_id': '',
                'selected_document_type': '',
                'selected_tags': '',
                'selected_ocr_status': '',
            }

        cleaned_data = self.search_form.cleaned_data
        return {
            'search_query': cleaned_data.get('q', ''),
            'selected_department_id': cleaned_data.get('department').pk if cleaned_data.get('department') else '',
            'selected_unit_id': cleaned_data.get('unit').pk if cleaned_data.get('unit') else '',
            'selected_document_type': cleaned_data.get('document_type', ''),
            'selected_tags': cleaned_data.get('tags', ''),
            'selected_ocr_status': cleaned_data.get('ocr_status', ''),
        }


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = filter_documents_for_user(self.request.user, Document.objects.for_list())
        context['document_count'] = queryset.count()
        context['my_document_count'] = queryset.filter(uploaded_by=self.request.user).count()
        context['ocr_backlog_count'] = queryset.filter(
            ocr_status__in=[OCRStatus.PENDING, OCRStatus.PROCESSING]
        ).count()
        context['recent_documents'] = queryset[:5]
        context['top_uploaders'] = (
            filter_documents_for_user(self.request.user, Document.objects.all())
            .values('uploaded_by__username')
            .annotate(total=Count('id'))
            .order_by('-total', 'uploaded_by__username')[:5]
        )
        context['scope_department'] = self.request.user.department
        context['scope_unit'] = self.request.user.unit
        return context


class DocumentUploadView(LoginRequiredMixin, UserScopedFormMixin, CreateView):
    model = Document
    form_class = DocumentUploadForm
    template_name = 'documents/upload.html'
    success_url = reverse_lazy('documents:document_list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not user_can_upload_documents(request.user):
            raise PermissionDenied('Your account is not assigned to a document-owning department.')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        if not (self.request.user.is_superuser or self.request.user.is_admin()):
            initial.setdefault('department', self.request.user.department_id)
            initial.setdefault('unit', self.request.user.unit_id)
        return initial

    def form_valid(self, form):
        form.instance.uploaded_by = self.request.user
        self.object = form.save()
        ocr_status = process_document_ocr(self.object)
        suggestions = apply_ocr_metadata_suggestions(self.object)
        messages.success(self.request, 'File uploaded successfully.')
        if ocr_status == OCRStatus.COMPLETED:
            messages.info(self.request, 'OCR scanned the file and filled metadata suggestions for review.')
        elif self.object.ocr_error:
            messages.warning(self.request, f'OCR status: {self.object.get_ocr_status_display()}. {self.object.ocr_error}')
        if suggestions.get('tags'):
            messages.info(self.request, f"Suggested tags: {', '.join(suggestions['tags'])}.")
        return redirect(f"{reverse('documents:edit_document', args=[self.object.pk])}?prefill=1")


class DocumentListView(DocumentFilterMixin, AccessibleDocumentMixin, ListView):
    model = Document
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        return self.filter_document_queryset(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.search_form
        context.update(self.document_filter_context())
        return context


class DocumentDetailView(AccessibleDocumentMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_queryset(self):
        return filter_documents_for_user(
            self.request.user,
            Document.objects.select_related('uploaded_by', 'department', 'unit').prefetch_related('tags'),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_edit'] = user_can_edit_document(self.request.user, self.object)
        context['can_delete'] = user_can_delete_document(self.request.user, self.object)
        return context


class DocumentUpdateView(LoginRequiredMixin, UserScopedFormMixin, UpdateView):
    model = Document
    form_class = DocumentMetadataForm
    template_name = 'documents/document_edit.html'

    def get_queryset(self):
        return editable_documents_for_user(
            self.request.user,
            Document.objects.select_related('department', 'unit', 'uploaded_by').prefetch_related('tags'),
        )

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Document details updated successfully.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['prefill_from_ocr'] = self.request.GET.get('prefill') == '1'
        return context

    def get_success_url(self):
        return reverse('documents:document_detail', args=[self.object.pk])


class DocumentDeleteView(LoginRequiredMixin, DeleteView):
    model = Document
    template_name = 'documents/document_delete.html'
    success_url = reverse_lazy('documents:document_list')
    context_object_name = 'document'

    def get_queryset(self):
        return deletable_documents_for_user(self.request.user, Document.objects.select_related('department', 'unit', 'uploaded_by'))

    def form_valid(self, form):
        if self.object.file:
            self.object.file.delete(save=False)
        messages.success(self.request, 'Document deleted successfully.')
        return super().form_valid(form)


def _serialize_document(document, request, *, include_body=False):
    payload = {
        'id': str(document.pk),
        'title': document.title,
        'document_type': document.document_type,
        'document_type_label': document.get_document_type_display(),
        'department': document.department.name if document.department else None,
        'unit': document.unit.name if document.unit else None,
        'ocr_status': document.ocr_status,
        'uploaded_by': document.uploaded_by.username,
        'created_at': document.created_at.isoformat(),
        'updated_at': document.updated_at.isoformat(),
        'file_url': request.build_absolute_uri(document.file.url) if document.file else None,
        'tags': [tag.name for tag in document.tags.all()],
    }
    if include_body:
        payload.update(
            {
                'description': document.description,
                'ocr_text': document.ocr_text,
                'ocr_error': document.ocr_error,
            }
        )
    rank = getattr(document, 'rank', None)
    if rank is not None:
        payload['search_rank'] = float(rank)
    return payload


class DocumentApiListView(DocumentFilterMixin, LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        base_queryset = filter_documents_for_user(
            request.user,
            Document.objects.for_list(),
        )
        queryset = self.filter_document_queryset(base_queryset)
        paginator, page_obj, page_size = paginate_queryset(
            queryset,
            page_number=request.GET.get('page', 1),
            page_size=request.GET.get('page_size', 20),
        )

        return JsonResponse(
            {
                'count': paginator.count,
                'page': page_obj.number,
                'page_size': page_size,
                'num_pages': paginator.num_pages,
                'results': [_serialize_document(document, request) for document in page_obj.object_list],
            }
        )


class DocumentApiDetailView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        queryset = filter_documents_for_user(
            request.user,
            Document.objects.select_related('uploaded_by', 'department', 'unit').prefetch_related('tags'),
        )
        document = get_object_or_404(queryset, pk=pk)
        return JsonResponse(_serialize_document(document, request, include_body=True))


class DocumentBrowseView(LoginRequiredMixin, TemplateView):
    template_name = 'documents/browse.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        department_slug = self.kwargs.get('department_slug')
        selected_year = self.kwargs.get('year')
        selected_type = self.kwargs.get('document_type')

        documents = filter_documents_for_user(
            self.request.user,
            Document.objects.select_related('department', 'unit', 'uploaded_by').prefetch_related('tags'),
        )
        department_rows = (
            documents
            .filter(department__isnull=False)
            .values('department__name', 'department__slug')
            .distinct()
            .order_by('department__name')
        )
        departments = [{'name': row['department__name'], 'slug': row['department__slug']} for row in department_rows]

        selected_department = None
        years = []
        types = []
        folder_docs = None
        page_obj = None

        if department_slug:
            documents = documents.filter(department__slug=department_slug)
            selected_row = documents.values('department__name', 'department__slug').first()
            if selected_row:
                selected_department = {'name': selected_row['department__name'], 'slug': selected_row['department__slug']}
                years = [date.year for date in documents.dates('created_at', 'year', order='DESC')]

        if selected_department and selected_year:
            documents = documents.filter(created_at__year=selected_year)
            types = list(
                documents.values_list('document_type', flat=True).distinct().order_by('document_type')
            )

        if selected_department and selected_year and selected_type:
            documents = documents.filter(document_type=selected_type).order_by('-created_at')
            _, page_obj, _ = paginate_queryset(
                documents,
                page_number=self.request.GET.get('page', 1),
                page_size=25,
            )
            folder_docs = page_obj.object_list

        context.update(
            {
                'departments': departments,
                'selected_department': selected_department,
                'years': years,
                'types': [(value, dict(DOC_TYPE_CHOICES).get(value, value.title())) for value in types],
                'selected_year': selected_year,
                'selected_type': selected_type,
                'selected_type_label': dict(DOC_TYPE_CHOICES).get(selected_type, selected_type),
                'folder_docs': folder_docs,
                'page_obj': page_obj,
            }
        )
        return context
