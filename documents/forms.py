from django import forms

from accounts.models import Department, Unit

from .models import Document
from .utils import (
    available_departments_for_user,
    available_units_for_user,
    user_can_assign_department,
    user_can_assign_unit,
)


class BootstrapFormMixin:
    def _apply_bootstrap_classes(self):
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                css_class = 'form-select'
            else:
                css_class = 'form-control'
            field.widget.attrs['class'] = css_class


class OrganizationScopedFormMixin(BootstrapFormMixin):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self._configure_org_fields()
        self._apply_bootstrap_classes()

    def _configure_org_fields(self):
        department_queryset = available_departments_for_user(self.user) if self.user else Department.objects.none()
        self.fields['department'].queryset = department_queryset
        self.fields['department'].required = True
        self.fields['unit'].required = False
        self.fields['unit'].queryset = self._available_units_for_selected_department()

        if self.user and not (self.user.is_superuser or self.user.is_admin()):
            if self.user.department_id:
                self.fields['department'].initial = self.user.department_id
            if self.user.unit_id:
                self.fields['unit'].initial = self.user.unit_id

    def _selected_department(self):
        department_value = None
        if self.is_bound:
            department_value = self.data.get(self.add_prefix('department'))
        elif self.instance and getattr(self.instance, 'department_id', None):
            department_value = self.instance.department_id
        else:
            department_value = self.initial.get('department') if hasattr(self, 'initial') else None

        if isinstance(department_value, Department):
            return department_value
        if department_value:
            try:
                return Department.objects.get(pk=department_value)
            except (Department.DoesNotExist, ValueError, TypeError):
                return None
        return None

    def _available_units_for_selected_department(self):
        department = self._selected_department()
        return available_units_for_user(self.user, department=department) if self.user else Unit.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        department = cleaned_data.get('department')
        unit = cleaned_data.get('unit')

        if department and self.user and not user_can_assign_department(self.user, department):
            self.add_error('department', 'You can only assign documents inside your department.')

        if unit and department and unit.department_id != department.id:
            self.add_error('unit', 'Selected unit must belong to the selected department.')

        if unit and self.user and not user_can_assign_unit(self.user, unit):
            self.add_error('unit', 'You cannot assign documents to this unit.')

        return cleaned_data


class DocumentUploadForm(OrganizationScopedFormMixin, forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'description', 'department', 'unit', 'file']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Quarterly finance report'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Add a helpful summary for search and review.'}),
            'file': forms.ClearableFileInput(attrs={'accept': '.pdf,.png,.jpg,.jpeg,.tif,.tiff'}),
        }


class DocumentMetadataForm(OrganizationScopedFormMixin, forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'description', 'department', 'unit']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Quarterly finance report'}),
            'description': forms.Textarea(attrs={'rows': 4}),
        }


class DocumentSearchForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Search by title, description, or OCR text'}),
    )
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = available_departments_for_user(user) if user else Department.objects.none()
        self.fields['unit'].queryset = self._available_units()
        self._apply_bootstrap_classes()

    def _selected_department(self):
        department_value = self.data.get(self.add_prefix('department')) if self.is_bound else self.initial.get('department')
        if isinstance(department_value, Department):
            return department_value
        if department_value:
            try:
                return Department.objects.get(pk=department_value)
            except (Department.DoesNotExist, ValueError, TypeError):
                return None
        return None

    def _available_units(self):
        department = self._selected_department()
        return available_units_for_user(self.user, department=department) if self.user else Unit.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        department = cleaned_data.get('department')
        unit = cleaned_data.get('unit')

        if department and self.user and not user_can_assign_department(self.user, department):
            self.add_error('department', 'You can only filter within your department.')

        if unit and self.user and not user_can_assign_unit(self.user, unit):
            self.add_error('unit', 'You cannot filter by this unit.')

        if unit and department and unit.department_id != department.id:
            self.add_error('unit', 'Selected unit must belong to the selected department.')

        return cleaned_data
