import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Company, Department, Unit

from .models import Document, OCRStatus

User = get_user_model()
TEST_MEDIA_ROOT = Path(tempfile.mkdtemp())


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class DocumentAccessTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.company = Company.objects.create(name='Acme Corp')
        self.finance = Department.objects.create(company=self.company, name='Finance')
        self.legal = Department.objects.create(company=self.company, name='Legal')
        self.payroll = Unit.objects.create(department=self.finance, name='Payroll')
        self.audit = Unit.objects.create(department=self.finance, name='Audit')
        self.contracts = Unit.objects.create(department=self.legal, name='Contracts')

        self.admin = User.objects.create_user(
            username='admin',
            password='secret123',
            role=User.ROLE_ADMIN,
            company=self.company,
        )
        self.manager = User.objects.create_user(
            username='manager',
            password='secret123',
            role=User.ROLE_MANAGER,
            company=self.company,
            department=self.finance,
            unit=self.payroll,
        )
        self.staff = User.objects.create_user(
            username='staff',
            password='secret123',
            role=User.ROLE_STAFF,
            company=self.company,
            department=self.finance,
            unit=self.payroll,
        )
        self.teammate = User.objects.create_user(
            username='teammate',
            password='secret123',
            role=User.ROLE_STAFF,
            company=self.company,
            department=self.finance,
            unit=self.audit,
        )
        self.outsider = User.objects.create_user(
            username='outsider',
            password='secret123',
            role=User.ROLE_STAFF,
            company=self.company,
            department=self.legal,
            unit=self.contracts,
        )

        self.payroll_doc = self._create_document(
            title='Payroll Register',
            uploaded_by=self.staff,
            department=self.finance,
            unit=self.payroll,
        )
        self.audit_doc = self._create_document(
            title='Audit Notes',
            uploaded_by=self.teammate,
            department=self.finance,
            unit=self.audit,
        )
        self.legal_doc = self._create_document(
            title='Vendor Contract',
            uploaded_by=self.outsider,
            department=self.legal,
            unit=self.contracts,
        )

    def _create_document(self, title, uploaded_by, department, unit):
        return Document.objects.create(
            title=title,
            description=f'{title} description',
            uploaded_by=uploaded_by,
            department=department,
            unit=unit,
            file=SimpleUploadedFile(f'{title.lower().replace(" ", "-")}.pdf', b'%PDF-1.4 sample', content_type='application/pdf'),
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('documents:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_staff_list_only_shows_department_documents(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.get(reverse('documents:document_list'))

        self.assertContains(response, 'Payroll Register')
        self.assertContains(response, 'Audit Notes')
        self.assertNotContains(response, 'Vendor Contract')

    def test_staff_upload_form_is_scoped_to_own_department_and_unit(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.get(reverse('documents:upload_document'))
        form = response.context['form']

        self.assertEqual(list(form.fields['department'].queryset), [self.finance])
        self.assertEqual(list(form.fields['unit'].queryset), [self.payroll])

    def test_staff_cannot_upload_to_other_department(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.post(
            reverse('documents:upload_document'),
            {
                'title': 'Illegal Upload',
                'description': 'Should be rejected',
                'department': self.legal.pk,
                'unit': self.contracts.pk,
                'file': SimpleUploadedFile('illegal.pdf', b'%PDF-1.4 sample', content_type='application/pdf'),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice. That choice is not one of the available choices.')
        self.assertFalse(Document.objects.filter(title='Illegal Upload').exists())

    def test_staff_can_edit_own_document_but_not_teammate_document(self):
        self.client.login(username='staff', password='secret123')

        own_response = self.client.post(
            reverse('documents:edit_document', args=[self.payroll_doc.pk]),
            {
                'title': 'Payroll Register Revised',
                'description': self.payroll_doc.description,
                'department': self.finance.pk,
                'unit': self.payroll.pk,
            },
        )
        teammate_response = self.client.get(reverse('documents:edit_document', args=[self.audit_doc.pk]))

        self.assertRedirects(own_response, reverse('documents:document_detail', args=[self.payroll_doc.pk]))
        self.payroll_doc.refresh_from_db()
        self.assertEqual(self.payroll_doc.title, 'Payroll Register Revised')
        self.assertEqual(teammate_response.status_code, 404)

    def test_manager_can_delete_department_document(self):
        self.client.login(username='manager', password='secret123')

        response = self.client.post(reverse('documents:delete_document', args=[self.audit_doc.pk]))

        self.assertRedirects(response, reverse('documents:document_list'))
        self.assertFalse(Document.objects.filter(pk=self.audit_doc.pk).exists())

    def test_manager_cannot_access_other_department_document(self):
        self.client.login(username='manager', password='secret123')

        response = self.client.get(reverse('documents:document_detail', args=[self.legal_doc.pk]))

        self.assertEqual(response.status_code, 404)

    def test_admin_can_filter_document_list_by_department(self):
        self.client.login(username='admin', password='secret123')

        response = self.client.get(
            reverse('documents:document_list'),
            {'department': self.legal.pk},
        )

        self.assertContains(response, 'Vendor Contract')
        self.assertNotContains(response, 'Payroll Register')

    def test_search_matches_ocr_text(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.ocr_text = 'confidential payroll totals for april'
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.save(update_fields=['ocr_text', 'ocr_status', 'updated_at'])

        response = self.client.get(reverse('documents:document_list'), {'q': 'confidential'})

        self.assertContains(response, 'Payroll Register')
        self.assertNotContains(response, 'Vendor Contract')

    def test_document_list_is_paginated(self):
        self.client.login(username='admin', password='secret123')
        for index in range(19):
            self._create_document(
                title=f'Extra Document {index}',
                uploaded_by=self.admin,
                department=self.finance,
                unit=self.payroll,
            )

        response = self.client.get(reverse('documents:document_list'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['documents']), 20)

    @patch('documents.views.process_document_ocr')
    def test_upload_runs_ocr_processing(self, process_document_ocr_mock):
        self.client.login(username='admin', password='secret123')

        def mark_ocr_complete(document):
            document.ocr_text = 'scanned invoice number 42'
            document.ocr_status = OCRStatus.COMPLETED
            document.ocr_error = ''
            document.save(update_fields=['ocr_text', 'ocr_status', 'ocr_error', 'updated_at'])
            return OCRStatus.COMPLETED

        process_document_ocr_mock.side_effect = mark_ocr_complete

        response = self.client.post(
            reverse('documents:upload_document'),
            {
                'title': 'Scanned Invoice',
                'description': 'OCR test',
                'department': self.finance.pk,
                'unit': self.payroll.pk,
                'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.4 sample', content_type='application/pdf'),
            },
            follow=True,
        )

        document = Document.objects.get(title='Scanned Invoice')
        self.assertEqual(document.ocr_status, OCRStatus.COMPLETED)
        self.assertEqual(document.ocr_text, 'scanned invoice number 42')
        self.assertContains(response, 'OCR text was extracted and added to search.')

    def test_upload_rejects_files_over_ten_megabytes(self):
        self.client.login(username='admin', password='secret123')
        oversized_file = SimpleUploadedFile(
            'large.pdf',
            b'a' * (10 * 1024 * 1024 + 1),
            content_type='application/pdf',
        )

        response = self.client.post(
            reverse('documents:upload_document'),
            {
                'title': 'Too Large',
                'description': 'This should fail',
                'department': self.finance.pk,
                'unit': self.payroll.pk,
                'file': oversized_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'File size must not exceed 10 MB.')
