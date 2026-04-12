from django.core.paginator import Paginator

from accounts.models import Department, Unit

from .models import Document


def available_departments_for_user(user):
    queryset = Department.objects.select_related('company').order_by('company__name', 'name')
    if user.is_superuser or user.is_admin():
        return queryset
    if user.department_id:
        return queryset.filter(pk=user.department_id)
    return queryset.none()


def available_units_for_user(user, department=None):
    queryset = Unit.objects.select_related('department', 'department__company').order_by(
        'department__company__name',
        'department__name',
        'name',
    )

    if department is not None:
        queryset = queryset.filter(department=department)

    if user.is_superuser or user.is_admin():
        return queryset

    if not user.department_id:
        return queryset.none()

    queryset = queryset.filter(department_id=user.department_id)
    if user.is_manager():
        return queryset

    if user.unit_id:
        return queryset.filter(pk=user.unit_id)

    return queryset.none()


def user_can_upload_documents(user):
    return user.is_authenticated and (
        user.is_superuser or user.is_admin() or user.department_id is not None
    )


def user_can_assign_department(user, department):
    if department is None:
        return False
    if user.is_superuser or user.is_admin():
        return True
    return department.pk == user.department_id


def user_can_assign_unit(user, unit):
    if unit is None:
        return True
    if user.is_superuser or user.is_admin():
        return True
    if not user.department_id or unit.department_id != user.department_id:
        return False
    if user.is_manager():
        return True
    if user.unit_id:
        return unit.pk == user.unit_id
    return False


def filter_documents_for_user(user, queryset=None):
    if queryset is None:
        queryset = Document.objects.all()
    if user.is_superuser or user.is_admin():
        return queryset
    if user.department_id:
        return queryset.filter(department_id=user.department_id)
    return queryset.none()


def editable_documents_for_user(user, queryset=None):
    queryset = filter_documents_for_user(user, queryset)
    if user.is_superuser or user.is_admin():
        return queryset
    if user.is_manager():
        return queryset
    if user.is_staff_role():
        return queryset.filter(uploaded_by=user)
    return queryset.none()


def deletable_documents_for_user(user, queryset=None):
    queryset = filter_documents_for_user(user, queryset)
    if user.is_superuser or user.is_admin():
        return queryset
    if user.is_manager():
        return queryset
    return queryset.none()


def user_can_view_document(user, document):
    return filter_documents_for_user(user, Document.objects.filter(pk=document.pk)).exists()


def user_can_edit_document(user, document):
    return editable_documents_for_user(user, Document.objects.filter(pk=document.pk)).exists()


def user_can_delete_document(user, document):
    return deletable_documents_for_user(user, Document.objects.filter(pk=document.pk)).exists()


def normalize_tag_names(raw_tags):
    if not raw_tags:
        return []
    if isinstance(raw_tags, str):
        values = raw_tags.split(',')
    else:
        values = raw_tags
    return [value.strip().lower() for value in values if value and value.strip()]


def apply_document_filters(queryset, *, query=None, department=None, unit=None, ocr_status=None, document_type=None, tags=None):
    if query:
        queryset = queryset.search(query)
    if department:
        queryset = queryset.filter(department=department)
    if unit:
        queryset = queryset.filter(unit=unit)
    if ocr_status:
        queryset = queryset.filter(ocr_status=ocr_status)
    if document_type:
        queryset = queryset.filter(document_type=document_type)
    normalized_tags = normalize_tag_names(tags)
    if normalized_tags:
        for tag_name in normalized_tags:
            queryset = queryset.filter(tags__name=tag_name)
        queryset = queryset.distinct()
    return queryset


def paginate_queryset(queryset, *, page_number=1, page_size=20, max_page_size=100):
    try:
        safe_page_size = int(page_size)
    except (TypeError, ValueError):
        safe_page_size = 20
    safe_page_size = max(1, min(safe_page_size, max_page_size))
    paginator = Paginator(queryset, safe_page_size)
    page_obj = paginator.get_page(page_number)
    return paginator, page_obj, safe_page_size
