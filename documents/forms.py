from django import forms
from .models import Document, Tag, DOC_TYPE_CHOICES
from accounts.models import Department, Unit

class DocumentUploadForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['file', 'department', 'unit', 'document_type']
        widgets = {
            'document_type': forms.Select(choices=DOC_TYPE_CHOICES),
        }

class DocumentMetadataForm(forms.ModelForm):
    tags = forms.CharField(required=False, help_text='Comma-separated tags')

    class Meta:
        model = Document
        fields = ['title', 'description', 'department', 'unit', 'document_type', 'tags']

    def clean_tags(self):
        tags_text = self.cleaned_data.get('tags', '')
        return [tag.strip().lower() for tag in tags_text.split(',') if tag.strip()]

    def save(self, commit=True):
        document = super().save(commit=False)
        if commit:
            document.save()
            tags = []
            for name in self.cleaned_data.get('tags', []):
                tag, _ = Tag.objects.get_or_create(name=name)
                tags.append(tag)
            document.tags.set(tags)
        return document

class DocumentSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Keywords')
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False)
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    document_type = forms.ChoiceField(choices=[('', 'All')] + DOC_TYPE_CHOICES, required=False)
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
