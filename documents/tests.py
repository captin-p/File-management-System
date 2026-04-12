import shutil
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Company, Department, Unit

from .models import AuditAction, AuditLog, Document, OCRJob, OCRJobStatus, OCRStatus, Tag

User = get_user_model()
TEST_MEDIA_ROOT = Path(__file__).resolve().parent.parent / '.test_media'
TEST_MEDIA_ROOT.mkdir(exist_ok=True)


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
            document_type='report',
            tags=['payroll', 'finance'],
        )
        self.audit_doc = self._create_document(
            title='Audit Notes',
            uploaded_by=self.teammate,
            department=self.finance,
            unit=self.audit,
            document_type='memo',
            tags=['audit'],
        )
        self.legal_doc = self._create_document(
            title='Vendor Contract',
            uploaded_by=self.outsider,
            department=self.legal,
            unit=self.contracts,
            document_type='contract',
            tags=['vendor', 'legal'],
        )

    def _create_document(self, title, uploaded_by, department, unit, document_type='other', tags=None):
        document = Document.objects.create(
            title=title,
            description=f'{title} description',
            document_type=document_type,
            uploaded_by=uploaded_by,
            department=department,
            unit=unit,
            file=SimpleUploadedFile(f'{title.lower().replace(" ", "-")}.pdf', b'%PDF-1.4 sample', content_type='application/pdf'),
        )
        if tags:
            for tag in tags:
                tag_obj, _ = Tag.objects.get_or_create(name=tag)
                document.tags.add(tag_obj)
        return document

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('documents:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_upload_requires_login(self):
        response = self.client.get(reverse('documents:upload_document'))
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
                'document_type': 'policy',
                'tags': 'payroll, revised',
                'department': self.finance.pk,
                'unit': self.payroll.pk,
            },
        )
        teammate_response = self.client.get(reverse('documents:edit_document', args=[self.audit_doc.pk]))

        self.assertRedirects(own_response, reverse('documents:document_detail', args=[self.payroll_doc.pk]))
        self.payroll_doc.refresh_from_db()
        self.assertEqual(self.payroll_doc.title, 'Payroll Register Revised')
        self.assertEqual(self.payroll_doc.document_type, 'policy')
        self.assertEqual(sorted(self.payroll_doc.tags.values_list('name', flat=True)), ['payroll', 'revised'])
        edit_log = self.payroll_doc.audit_logs.get(action=AuditAction.EDIT, actor=self.staff)
        self.assertIn('title', edit_log.metadata['changed_fields'])
        self.assertEqual(teammate_response.status_code, 404)

    def test_manager_can_delete_department_document(self):
        self.client.login(username='manager', password='secret123')

        response = self.client.post(reverse('documents:delete_document', args=[self.audit_doc.pk]))

        self.assertRedirects(response, reverse('documents:document_list'))
        self.assertFalse(Document.objects.filter(pk=self.audit_doc.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.DELETE,
                actor=self.manager,
                metadata__title='Audit Notes',
            ).exists()
        )

    def test_manager_cannot_access_other_department_document(self):
        self.client.login(username='manager', password='secret123')

        response = self.client.get(reverse('documents:document_detail', args=[self.legal_doc.pk]))

        self.assertEqual(response.status_code, 404)

    def test_document_detail_records_view_audit_log(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.get(reverse('documents:document_detail', args=[self.payroll_doc.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.payroll_doc.audit_logs.filter(
                action=AuditAction.VIEW,
                actor=self.staff,
            ).exists()
        )

    def test_admin_can_filter_document_list_by_department(self):
        self.client.login(username='admin', password='secret123')

        response = self.client.get(
            reverse('documents:document_list'),
            {'department': self.legal.pk},
        )

        self.assertContains(response, 'Vendor Contract')
        self.assertNotContains(response, 'Payroll Register')

    def test_admin_can_filter_document_list_by_tag_and_type(self):
        self.client.login(username='admin', password='secret123')

        response = self.client.get(
            reverse('documents:document_list'),
            {'tags': 'payroll', 'document_type': 'report'},
        )

        self.assertContains(response, 'Payroll Register')
        self.assertNotContains(response, 'Audit Notes')
        self.assertNotContains(response, 'Vendor Contract')

    def test_document_api_list_returns_paginated_json(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.ocr_text = 'payroll values'
        self.payroll_doc.save(update_fields=['ocr_status', 'ocr_text', 'updated_at'])

        response = self.client.get(
            reverse('documents:document_api_list'),
            {'page_size': 1, 'ocr_status': OCRStatus.COMPLETED},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['page_size'], 1)
        self.assertEqual(payload['results'][0]['title'], 'Payroll Register')
        self.assertEqual(payload['results'][0]['ocr_status'], OCRStatus.COMPLETED)
        self.assertEqual(payload['results'][0]['document_type'], 'report')
        self.assertEqual(payload['results'][0]['tags'], ['finance', 'payroll'])

    def test_document_api_detail_respects_scope(self):
        self.client.login(username='manager', password='secret123')

        response = self.client.get(reverse('documents:document_api_detail', args=[self.legal_doc.pk]))

        self.assertEqual(response.status_code, 404)

    def test_search_matches_ocr_text(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.ocr_text = 'confidential payroll totals for april'
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.save(update_fields=['ocr_text', 'ocr_status', 'updated_at'])

        response = self.client.get(reverse('documents:document_list'), {'q': 'confidential'})

        self.assertContains(response, 'Payroll Register')
        self.assertNotContains(response, 'Vendor Contract')

    def test_archive_browser_filters_by_department_year_and_type(self):
        self.client.login(username='admin', password='secret123')
        year = self.payroll_doc.created_at.year

        response = self.client.get(
            reverse('documents:browse_folder_type', args=[self.finance.slug, year, 'report'])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Payroll Register')
        self.assertNotContains(response, 'Audit Notes')
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
    def test_upload_scans_and_prefills_metadata_form(self, process_document_ocr_mock):
        self.client.login(username='admin', password='secret123')

        def mark_ocr_complete(document):
            document.ocr_text = 'Invoice 4242\nBill to Acme Corp\nTotal due 240.00'
            document.ocr_status = OCRStatus.COMPLETED
            document.ocr_error = ''
            document.save(update_fields=['ocr_text', 'ocr_status', 'ocr_error', 'updated_at'])
            return OCRStatus.COMPLETED

        process_document_ocr_mock.side_effect = mark_ocr_complete

        response = self.client.post(
            reverse('documents:upload_document'),
            {
                'department': self.finance.pk,
                'unit': self.payroll.pk,
                'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.4 sample', content_type='application/pdf'),
            },
            follow=True,
        )

        document = Document.objects.get(title='Invoice 4242')
        self.assertRedirects(
            response,
            f"{reverse('documents:edit_document', args=[document.pk])}?prefill=1",
        )
        self.assertEqual(document.ocr_status, OCRStatus.COMPLETED)
        self.assertEqual(document.ocr_text, 'Invoice 4242\nBill to Acme Corp\nTotal due 240.00')
        self.assertEqual(document.document_type, 'invoice')
        self.assertIn('Bill to Acme Corp', document.description)
        self.assertEqual(sorted(document.tags.values_list('name', flat=True)), ['acme', 'bill', 'corp', 'due'])
        self.assertEqual(document.ocr_jobs.count(), 0)
        process_document_ocr_mock.assert_called_once()
        upload_log = document.audit_logs.get(action=AuditAction.UPLOAD, actor=self.admin)
        self.assertEqual(upload_log.metadata['ocr_status'], OCRStatus.COMPLETED)
        self.assertContains(response, 'OCR filled these fields from the uploaded file.')
        self.assertContains(response, 'OCR scanned the file and filled metadata suggestions for review.')

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
                'document_type': 'other',
                'tags': '',
                'department': self.finance.pk,
                'unit': self.payroll.pk,
                'file': oversized_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'File size must not exceed 10 MB.')

    @patch('documents.management.commands.reprocess_ocr.process_document_ocr')
    def test_reprocess_ocr_command_filters_documents(self, process_document_ocr_mock):
        self.payroll_doc.ocr_status = OCRStatus.FAILED
        self.payroll_doc.save(update_fields=['ocr_status', 'updated_at'])
        self.audit_doc.ocr_status = OCRStatus.SKIPPED
        self.audit_doc.save(update_fields=['ocr_status', 'updated_at'])
        self.legal_doc.ocr_status = OCRStatus.COMPLETED
        self.legal_doc.save(update_fields=['ocr_status', 'updated_at'])

        def fake_process(document):
            document.ocr_status = OCRStatus.COMPLETED
            document.save(update_fields=['ocr_status', 'updated_at'])
            return OCRStatus.COMPLETED

        process_document_ocr_mock.side_effect = fake_process

        stdout = StringIO()
        call_command('reprocess_ocr', '--status', OCRStatus.FAILED, '--status', OCRStatus.SKIPPED, stdout=stdout)

        self.assertEqual(process_document_ocr_mock.call_count, 2)
        processed_ids = {str(call.args[0].pk) for call in process_document_ocr_mock.call_args_list}
        self.assertEqual(processed_ids, {str(self.payroll_doc.pk), str(self.audit_doc.pk)})
        self.assertIn('Processed 2 document(s). completed=2', stdout.getvalue())

    def test_queue_ocr_jobs_skips_active_jobs(self):
        self.payroll_doc.ocr_status = OCRStatus.FAILED
        self.payroll_doc.save(update_fields=['ocr_status', 'updated_at'])

        first_stdout = StringIO()
        call_command('queue_ocr_jobs', '--document-id', self.payroll_doc.pk, stdout=first_stdout)
        second_stdout = StringIO()
        call_command('queue_ocr_jobs', '--document-id', self.payroll_doc.pk, stdout=second_stdout)

        self.assertEqual(self.payroll_doc.ocr_jobs.count(), 1)
        self.assertTrue(
            self.payroll_doc.audit_logs.filter(action=AuditAction.OCR_QUEUE).exists()
        )
        self.assertIn('Queued 1 OCR job(s). skipped_active=0', first_stdout.getvalue())
        self.assertIn('Queued 0 OCR job(s). skipped_active=1', second_stdout.getvalue())

    @patch('documents.services.process_document_ocr')
    def test_process_ocr_queue_processes_queued_job(self, process_document_ocr_mock):
        job = OCRJob.objects.create(document=self.payroll_doc)

        def fake_process(document):
            document.ocr_text = 'queued payroll text'
            document.ocr_status = OCRStatus.COMPLETED
            document.ocr_error = ''
            document.save(update_fields=['ocr_text', 'ocr_status', 'ocr_error', 'updated_at'])
            return OCRStatus.COMPLETED

        process_document_ocr_mock.side_effect = fake_process

        stdout = StringIO()
        call_command('process_ocr_queue', '--limit', '1', stdout=stdout)

        job.refresh_from_db()
        self.payroll_doc.refresh_from_db()
        self.assertEqual(job.status, OCRJobStatus.COMPLETED)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(self.payroll_doc.ocr_status, OCRStatus.COMPLETED)
        self.assertEqual(self.payroll_doc.ocr_text, 'queued payroll text')
        self.assertTrue(
            self.payroll_doc.audit_logs.filter(action=AuditAction.OCR_PROCESS).exists()
        )
        self.assertIn('Processed 1 OCR job(s). completed=1, failed=0', stdout.getvalue())

    def test_rebuild_search_index_command_matches_database_backend(self):
        stdout = StringIO()

        call_command('rebuild_search_index', stdout=stdout)

        if connection.vendor == 'postgresql':
            self.assertIn('Rebuilt search vectors for 3 of 3 document(s).', stdout.getvalue())
        else:
            self.assertIn('Search index rebuild skipped', stdout.getvalue())
