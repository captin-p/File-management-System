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
