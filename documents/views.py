from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import DocumentMetadataForm, DocumentSearchForm, DocumentUploadForm
from .models import Document, OCRStatus
from .services import process_document_ocr
from .utils import (
    deletable_documents_for_user,
    editable_documents_for_user,
    filter_documents_for_user,
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


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = filter_documents_for_user(self.request.user, Document.objects.for_list())
        context['document_count'] = queryset.count()
        context['my_document_count'] = queryset.filter(uploaded_by=self.request.user).count()
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
        response = super().form_valid(form)
        ocr_status = process_document_ocr(self.object)
        messages.success(self.request, 'Document uploaded successfully.')
        if ocr_status == OCRStatus.COMPLETED:
            messages.info(self.request, 'OCR text was extracted and added to search.')
        elif self.object.ocr_error:
            messages.warning(self.request, f'OCR status: {self.object.get_ocr_status_display()}. {self.object.ocr_error}')
        return response


class DocumentListView(AccessibleDocumentMixin, ListView):
    model = Document
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'
    paginate_by = 20

    def get_queryset(self):
        self.search_form = DocumentSearchForm(self.request.GET or None, user=self.request.user)
        queryset = super().get_queryset()

        if self.search_form.is_valid():
            data = self.search_form.cleaned_data
            queryset = queryset.search(data.get('q'))
            if data.get('department'):
                queryset = queryset.filter(department=data['department'])
            if data.get('unit'):
                queryset = queryset.filter(unit=data['unit'])

        if not self.request.GET.get('q'):
            queryset = queryset.order_by('-created_at')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.search_form
        context['search_query'] = self.search_form.cleaned_data.get('q', '') if self.search_form.is_valid() else ''
        context['selected_department_id'] = self.search_form.cleaned_data.get('department').pk if self.search_form.is_valid() and self.search_form.cleaned_data.get('department') else ''
        context['selected_unit_id'] = self.search_form.cleaned_data.get('unit').pk if self.search_form.is_valid() and self.search_form.cleaned_data.get('unit') else ''
        return context


class DocumentDetailView(AccessibleDocumentMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_queryset(self):
        return filter_documents_for_user(
            self.request.user,
            Document.objects.select_related('uploaded_by', 'department', 'unit'),
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
        return editable_documents_for_user(self.request.user, Document.objects.select_related('department', 'unit', 'uploaded_by'))

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Document details updated successfully.')
        return response

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
