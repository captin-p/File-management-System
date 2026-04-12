from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from accounts.models import Company, Department, Unit
from .models import Document, Tag
from django.core.files.uploadedfile import SimpleUploadedFile

User = get_user_model()

class DocumentWorkflowTest(TestCase):
    def setUp(self):
        company = Company.objects.create(name='TestCo')
        department = Department.objects.create(company=company, name='HR')
        unit = Unit.objects.create(department=department, name='Payroll')
        self.manager = User.objects.create_user(username='manager', password='secret', role=User.ROLE_MANAGER, company=company, department=department, unit=unit)
        self.client = Client()
        self.client.login(username='manager', password='secret')

    def test_upload_and_list_document(self):
        file_data = SimpleUploadedFile('test.txt', b'Invoice 12345', content_type='text/plain')
        response = self.client.post('/upload/', {'file': file_data, 'department': self.manager.department.pk, 'unit': self.manager.unit.pk, 'document_type': 'invoice'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Document.objects.exists())

    def test_document_api_list_requires_login(self):
        self.client.logout()
        response = self.client.get('/api/documents/')
        self.assertEqual(response.status_code, 302)
