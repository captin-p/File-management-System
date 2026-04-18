from django import forms

from .models import Department, Unit


class DepartmentCreateForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name']

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.update({'class': 'form-control'})
        if self.company is not None:
            self.instance.company = self.company

    def save(self, commit=True):
        self.instance.company = self.company
        return super().save(commit=commit)


class UnitCreateForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['department', 'name']

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields['department'].widget.attrs.update({'class': 'form-select'})
        self.fields['name'].widget.attrs.update({'class': 'form-control'})
        queryset = Department.objects.none()
        if self.company is not None:
            queryset = Department.objects.filter(company=self.company).order_by('name')
        self.fields['department'].queryset = queryset
