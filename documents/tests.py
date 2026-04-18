import hashlib
import shutil
from datetime import date
from pathlib import Path
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError as KombuOperationalError

from accounts.models import Company, Department, Unit
from accounts.organization import DEFAULT_ORGANIZATION_STRUCTURE

from .models import AuditAction, AuditLog, Document, DocumentProcessingStatus, MetadataSource, OCRJob, OCRJobStatus, OCRStatus, Tag
from .services import (
    apply_ocr_metadata_suggestions,
    extracted_dates_from_ocr,
    extract_text_from_pdf,
    process_document_pipeline,
    queue_document_processing,
    suggested_description_from_ocr,
    suggested_extracted_date_from_ocr,
)
from .storage import stored_file_metadata, validate_document_file_integrity

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
            file=SimpleUploadedFile(
                f'{title.lower().replace(" ", "-")}.pdf',
                self._file_content(title),
                content_type='application/pdf',
            ),
        )
        if tags:
            for tag in tags:
                tag_obj, _ = Tag.objects.get_or_create(name=tag)
                document.tags.add(tag_obj)
        metadata = stored_file_metadata(document)
        document.file_size = metadata.file_size
        document.file_hash = metadata.file_hash
        document.processing_status = DocumentProcessingStatus.READY
        document.save(update_fields=['file_size', 'file_hash', 'processing_status', 'updated_at'])
        return document

    def _file_content(self, title):
        return f'%PDF-1.4 {title}'.encode()

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('documents:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_upload_requires_login(self):
        response = self.client.get(reverse('documents:upload_document'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_organization_settings_requires_login(self):
        response = self.client.get(reverse('accounts:organization_settings'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_admin_dashboard_shows_organization_setup_access(self):
        self.client.login(username='admin', password='secret123')

        response = self.client.get(reverse('documents:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Organization settings')
        self.assertContains(response, reverse('accounts:organization_settings'))
        self.assertTrue(response.context['can_manage_organization'])
        self.assertEqual(response.context['organization_company_name'], 'Acme Corp')
        self.assertTrue(response.context['organization_setup_needed'])

    def test_staff_cannot_access_organization_settings(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.get(reverse('accounts:organization_settings'))

        self.assertEqual(response.status_code, 403)

    def test_admin_can_seed_default_organization_from_settings_page(self):
        self.client.login(username='admin', password='secret123')

        response = self.client.post(
            reverse('accounts:organization_settings'),
            {'action': 'seed'},
            follow=True,
        )

        expected_departments = len(DEFAULT_ORGANIZATION_STRUCTURE)
        expected_units = sum(len(units) for units in DEFAULT_ORGANIZATION_STRUCTURE.values())

        self.assertRedirects(response, reverse('accounts:organization_settings'))
        self.assertEqual(Department.objects.filter(company=self.company).count(), expected_departments)
        self.assertEqual(Unit.objects.filter(department__company=self.company).count(), expected_units)
        self.assertContains(response, 'Organization settings ready for Acme Corp.')

    def test_admin_can_add_department_and_unit_from_settings_page(self):
        self.client.login(username='admin', password='secret123')

        add_department = self.client.post(
            reverse('accounts:organization_settings'),
            {'action': 'add_department', 'name': 'Research'},
        )
        add_unit = self.client.post(
            reverse('accounts:organization_settings'),
            {'action': 'add_unit', 'department': Department.objects.get(company=self.company, name='Research').pk, 'name': 'Innovation Lab'},
        )

        self.assertEqual(add_department.status_code, 302)
        self.assertEqual(add_unit.status_code, 302)
        self.assertTrue(Department.objects.filter(company=self.company, name='Research').exists())
        self.assertTrue(
            Unit.objects.filter(
                department__company=self.company,
                department__name='Research',
                name='Innovation Lab',
            ).exists()
        )

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
        self.assertIn(f'/policy/{self.payroll_doc.pk}.pdf', self.payroll_doc.file.name)
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

    def test_document_detail_hides_raw_ocr_text_and_shows_extraction_summary(self):
        self.client.login(username='staff', password='secret123')
        self.payroll_doc.ocr_text = 'PAYROLL REGISTER\nBODY SENTENCE ONLY FOR RAW OCR DISPLAY TEST\nPrepared for April processing'
        self.payroll_doc.description = 'PAYROLL REGISTER. Prepared for April processing'
        self.payroll_doc.headings = ['PAYROLL REGISTER', 'Prepared for April processing']
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.metadata_source = MetadataSource.AI
        self.payroll_doc.metadata_model = 'gpt-4.1-mini'
        self.payroll_doc.save(update_fields=['ocr_text', 'description', 'headings', 'ocr_status', 'metadata_source', 'metadata_model', 'updated_at'])

        response = self.client.get(reverse('documents:document_detail', args=[self.payroll_doc.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OCR extraction')
        self.assertContains(response, 'Extraction source')
        self.assertContains(response, 'Prefilled title')
        self.assertContains(response, 'Detected headings')
        self.assertContains(response, 'Description summary')
        self.assertContains(response, 'gpt-4.1-mini')
        self.assertNotContains(response, 'BODY SENTENCE ONLY FOR RAW OCR DISPLAY TEST')

    def test_document_detail_shows_inline_pdf_preview(self):
        self.client.login(username='staff', password='secret123')

        response = self.client.get(reverse('documents:document_detail', args=[self.payroll_doc.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'File preview')
        self.assertContains(response, 'Open full file')
        self.assertContains(response, '<iframe', html=False)
        self.assertContains(response, f'{self.payroll_doc.file.url}#view=FitH')

    def test_document_files_are_stored_by_department_year_and_type(self):
        expected_prefix = f'storage/{self.finance.slug}/{self.payroll_doc.created_at.year}/report/'

        self.assertTrue(self.payroll_doc.file.name.startswith(expected_prefix))
        self.assertEqual(Path(self.payroll_doc.file.name).name, f'{self.payroll_doc.pk}.pdf')
        self.assertEqual(self.payroll_doc.original_filename, 'payroll-register.pdf')
        self.assertEqual(self.payroll_doc.file_size, len(self._file_content('Payroll Register')))
        self.assertEqual(
            self.payroll_doc.file_hash,
            hashlib.sha256(self._file_content('Payroll Register')).hexdigest(),
        )

    def test_document_file_integrity_validation_uses_hash_and_size(self):
        self.assertTrue(validate_document_file_integrity(self.payroll_doc))

        self.payroll_doc.file_hash = '0' * 64

        with self.assertRaisesMessage(ValidationError, 'Stored file hash does not match'):
            validate_document_file_integrity(self.payroll_doc)

    def test_ocr_date_extraction_handles_common_formats(self):
        ocr_text = 'Issued 13/04/2026\nDue 2026-05-01\nSigned April 20, 2026'

        self.assertEqual(
            extracted_dates_from_ocr(ocr_text),
            [date(2026, 4, 13), date(2026, 5, 1), date(2026, 4, 20)],
        )
        self.assertEqual(suggested_extracted_date_from_ocr(ocr_text), date(2026, 4, 13))

    def test_suggested_description_prefers_heading_like_lines(self):
        ocr_text = (
            'ACME CORPORATION\n'
            'QUARTERLY PAYROLL REPORT\n'
            'APRIL 2026\n'
            'This paragraph contains body details that should stay out of the description summary.\n'
            'Payroll was processed successfully.'
        )

        description = suggested_description_from_ocr(ocr_text, title='ACME CORPORATION')

        self.assertEqual(description, 'QUARTERLY PAYROLL REPORT. APRIL 2026')

    @override_settings(AI_METADATA_ENABLED=True, OPENAI_API_KEY='test-key', AI_METADATA_MODEL='gpt-4.1-mini', AI_METADATA_USE_IMAGE=False)
    @patch('documents.services.extract_document_metadata_with_ai')
    def test_apply_ocr_metadata_suggestions_uses_ai_when_available(self, extract_document_metadata_with_ai_mock):
        self.payroll_doc.ocr_text = (
            'PAYROLL REGISTER\n'
            'APRIL 2026\n'
            'Processed salary ledger for monthly payroll.'
        )
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.save(update_fields=['ocr_text', 'ocr_status', 'updated_at'])
        extract_document_metadata_with_ai_mock.return_value = {
            'title': 'Payroll Register',
            'description': 'April 2026 salary ledger',
            'headings': ['PAYROLL REGISTER', 'APRIL 2026', 'Monthly salary ledger'],
            'document_type': 'report',
            'tags': ['payroll', 'salary', 'april'],
            'extracted_date': '2026-04-13',
            'metadata_source': 'ai',
            'metadata_model': 'gpt-4.1-mini',
        }

        result = apply_ocr_metadata_suggestions(self.payroll_doc)

        self.payroll_doc.refresh_from_db()
        self.assertEqual(self.payroll_doc.metadata_source, MetadataSource.AI)
        self.assertEqual(self.payroll_doc.metadata_model, 'gpt-4.1-mini')
        self.assertEqual(self.payroll_doc.title, 'Payroll Register')
        self.assertEqual(self.payroll_doc.description, 'April 2026 salary ledger')
        self.assertEqual(self.payroll_doc.headings, ['APRIL 2026', 'Monthly salary ledger'])
        self.assertEqual(self.payroll_doc.extracted_date, date(2026, 4, 13))
        self.assertEqual(sorted(self.payroll_doc.tags.values_list('name', flat=True)), ['april', 'payroll', 'salary'])
        self.assertEqual(result['metadata_source'], MetadataSource.AI)

    def test_document_api_detail_includes_ai_metadata_fields(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.headings = ['PAYROLL REGISTER', 'APRIL 2026']
        self.payroll_doc.metadata_source = MetadataSource.AI
        self.payroll_doc.metadata_model = 'gpt-4.1-mini'
        self.payroll_doc.save(update_fields=['headings', 'metadata_source', 'metadata_model', 'updated_at'])

        response = self.client.get(reverse('documents:document_api_detail', args=[self.payroll_doc.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['headings'], ['PAYROLL REGISTER', 'APRIL 2026'])
        self.assertEqual(payload['metadata_source'], MetadataSource.AI)
        self.assertEqual(payload['metadata_model'], 'gpt-4.1-mini')

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

    def test_admin_can_filter_document_list_by_extracted_date_range(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.extracted_date = date(2026, 4, 1)
        self.audit_doc.extracted_date = date(2026, 5, 15)
        self.legal_doc.extracted_date = date(2026, 6, 1)
        Document.objects.bulk_update(
            [self.payroll_doc, self.audit_doc, self.legal_doc],
            ['extracted_date'],
        )

        response = self.client.get(
            reverse('documents:document_list'),
            {'date_from': '2026-05-01', 'date_to': '2026-05-31'},
        )

        self.assertContains(response, 'Audit Notes')
        self.assertNotContains(response, 'Payroll Register')
        self.assertNotContains(response, 'Vendor Contract')

    def test_document_api_list_returns_paginated_json(self):
        self.client.login(username='admin', password='secret123')
        self.payroll_doc.ocr_status = OCRStatus.COMPLETED
        self.payroll_doc.ocr_text = 'payroll values'
        self.payroll_doc.extracted_date = date(2026, 4, 1)
        self.payroll_doc.save(update_fields=['ocr_status', 'ocr_text', 'extracted_date', 'updated_at'])

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
        self.assertEqual(payload['results'][0]['extracted_date'], '2026-04-01')
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

    def test_postgres_search_orders_by_rank_and_highlights_matches(self):
        if connection.vendor != 'postgresql':
            return

        self.client.login(username='admin', password='secret123')
        title_match = self._create_document(
            title='Zephyrrank Report',
            uploaded_by=self.admin,
            department=self.finance,
            unit=self.payroll,
            document_type='report',
        )
        ocr_match = self._create_document(
            title='Body Only Match',
            uploaded_by=self.admin,
            department=self.finance,
            unit=self.payroll,
            document_type='report',
        )
        ocr_match.ocr_text = 'This archived OCR page mentions zephyrrank once.'
        ocr_match.ocr_status = OCRStatus.COMPLETED
        ocr_match.save(update_fields=['ocr_text', 'ocr_status', 'updated_at'])

        response = self.client.get(reverse('documents:document_list'), {'q': 'zephyrrank'})
        documents = list(response.context['documents'])

        self.assertEqual(documents[0].pk, title_match.pk)
        self.assertGreater(documents[0].rank, documents[1].rank)
        self.assertContains(response, '<mark class="search-hit">Zephyrrank</mark>')

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

    @patch('documents.views.queue_document_processing')
    def test_upload_queues_background_processing(self, queue_document_processing_mock):
        self.client.login(username='admin', password='secret123')
        uploaded_content = b'%PDF-1.4 sample invoice'
        queue_document_processing_mock.return_value = {
            'queued': True,
            'fallback': False,
            'task_id': 'task-123',
        }

        response = self.client.post(
            reverse('documents:upload_document'),
            {
                'department': self.finance.pk,
                'unit': self.payroll.pk,
                'file': SimpleUploadedFile('invoice.pdf', uploaded_content, content_type='application/pdf'),
            },
            follow=True,
        )

        document = Document.objects.get(title='invoice')
        self.assertRedirects(
            response,
            reverse('documents:document_detail', args=[document.pk]),
        )
        self.assertEqual(document.processing_status, DocumentProcessingStatus.PROCESSING)
        self.assertEqual(document.ocr_status, OCRStatus.PENDING)
        self.assertEqual(document.file_hash, '')
        self.assertEqual(document.original_filename, 'invoice.pdf')
        self.assertEqual(document.file_size, len(uploaded_content))
        queue_document_processing_mock.assert_called_once()
        upload_log = document.audit_logs.get(action=AuditAction.UPLOAD, actor=self.admin)
        self.assertEqual(upload_log.metadata['queued'], True)
        self.assertEqual(upload_log.metadata['task_id'], 'task-123')
        self.assertContains(response, 'The document is being processed in the background.')

    @patch('documents.services.process_document_ocr')
    def test_processing_pipeline_hashes_scans_and_prefills_metadata(self, process_document_ocr_mock):
        uploaded_content = b'%PDF-1.4 sample invoice'
        document = Document.objects.create(
            title='invoice',
            description='',
            document_type='other',
            uploaded_by=self.admin,
            department=self.finance,
            unit=self.payroll,
            processing_status=DocumentProcessingStatus.PROCESSING,
            ocr_status=OCRStatus.PENDING,
            file=SimpleUploadedFile('invoice.pdf', uploaded_content, content_type='application/pdf'),
        )

        def mark_ocr_complete(document):
            document.ocr_text = 'Invoice 4242\nInvoice Date: 2026-04-13\nBill to Acme Corp\nTotal due 240.00'
            document.ocr_status = OCRStatus.COMPLETED
            document.ocr_error = ''
            document.save(update_fields=['ocr_text', 'ocr_status', 'ocr_error', 'updated_at'])
            return OCRStatus.COMPLETED

        process_document_ocr_mock.side_effect = mark_ocr_complete

        result = process_document_pipeline(document.pk)

        document.refresh_from_db()
        self.assertEqual(result['status'], DocumentProcessingStatus.READY)
        self.assertEqual(document.processing_status, DocumentProcessingStatus.READY)
        self.assertEqual(document.ocr_status, OCRStatus.COMPLETED)
        self.assertEqual(document.ocr_text, 'Invoice 4242\nInvoice Date: 2026-04-13\nBill to Acme Corp\nTotal due 240.00')
        self.assertEqual(document.document_type, 'invoice')
        self.assertEqual(document.extracted_date, date(2026, 4, 13))
        self.assertEqual(document.file_hash, hashlib.sha256(uploaded_content).hexdigest())
        self.assertTrue(document.file.name.startswith(f'storage/{self.finance.slug}/{document.created_at.year}/invoice/'))
        self.assertEqual(Path(document.file.name).name, f'{document.pk}.pdf')
        self.assertIn('Bill to Acme Corp', document.description)
        self.assertEqual(sorted(document.tags.values_list('name', flat=True)), ['acme', 'bill', 'corp', 'due'])
        self.assertEqual(document.ocr_jobs.count(), 0)
        process_document_ocr_mock.assert_called_once()

    @override_settings(OCR_PDF_MAX_PAGES=2, OCR_POPPLER_PATH='')
    def test_pdf_ocr_limits_extraction_to_first_two_pages(self):
        dependencies = {
            'convert_from_path': Mock(return_value=['page-1-image', 'page-2-image']),
            'pytesseract': Mock(),
        }
        dependencies['pytesseract'].image_to_string.side_effect = ['Page one text', 'Page two text']

        extracted_text = extract_text_from_pdf('sample.pdf', dependencies)

        self.assertEqual(extracted_text, 'Page one text\nPage two text')
        dependencies['convert_from_path'].assert_called_once_with(
            'sample.pdf',
            dpi=250,
            first_page=1,
            last_page=2,
        )
        self.assertEqual(dependencies['pytesseract'].image_to_string.call_count, 2)

    def test_processing_pipeline_rejects_duplicate_file_hash(self):
        document = Document.objects.create(
            title='payroll-copy',
            description='',
            document_type='other',
            uploaded_by=self.admin,
            department=self.finance,
            unit=self.payroll,
            processing_status=DocumentProcessingStatus.PROCESSING,
            ocr_status=OCRStatus.PENDING,
            file=SimpleUploadedFile(
                'payroll-copy.pdf',
                self._file_content('Payroll Register'),
                content_type='application/pdf',
            ),
        )

        result = process_document_pipeline(document.pk)

        document.refresh_from_db()
        self.assertEqual(result['status'], DocumentProcessingStatus.FAILED)
        self.assertEqual(document.processing_status, DocumentProcessingStatus.FAILED)
        self.assertEqual(document.ocr_status, OCRStatus.FAILED)
        self.assertEqual(document.file.name, '')
        self.assertIn('Payroll Register', document.ocr_error)

    @patch('documents.services.process_document_pipeline')
    @patch('documents.tasks.process_document_task.apply_async')
    def test_queue_document_processing_falls_back_when_redis_is_unavailable(self, apply_async_mock, pipeline_mock):
        apply_async_mock.side_effect = KombuOperationalError('redis unavailable')
        pipeline_mock.return_value = {'status': DocumentProcessingStatus.READY}

        result = queue_document_processing(self.payroll_doc, user=self.admin)

        self.assertFalse(result['queued'])
        self.assertTrue(result['fallback'])
        pipeline_mock.assert_called_once_with(str(self.payroll_doc.pk), fallback=True)

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
