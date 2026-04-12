from django.db.models import Q
from .models import Document


def filter_documents_for_user(user, queryset=None):
    if queryset is None:
        queryset = Document.objects.all()
    if user.is_superuser or user.is_admin():
        return queryset
    if user.department:
        return queryset.filter(department=user.department)
    return queryset.none()


def user_can_view_document(user, document):
    if user.is_superuser or user.is_admin():
        return True
    if not document.department or not user.department:
        return False
    return user.department == document.department


def user_can_edit_document(user, document):
    if user.is_superuser or user.is_admin():
        return True
    if not user.department or not document.department:
        return False
    if document.department != user.department:
        return False
    if user.is_manager():
        return True
    if user.is_staff_role():
        return document.created_by_id == user.id
    return False


def user_can_delete_document(user, document):
    if user.is_superuser or user.is_admin():
        return True
    if not user.department or not document.department:
        return False
    return user.is_manager() and document.department == user.department


def build_browse_hierarchy(user):
    documents = filter_documents_for_user(user)
    hierarchy = {}
    for document in documents.select_related('department'):
        department_name = document.department.name if document.department else 'Unassigned'
        department_slug = document.department.slug if document.department else 'unassigned'
        year = document.upload_date.year
        doc_type = document.document_type
        hierarchy.setdefault((department_name, department_slug), {}).setdefault(year, set()).add(doc_type)
    return hierarchy
