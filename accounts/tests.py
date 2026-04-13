from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from io import StringIO

from .models import Company, Department, Unit
from .organization import DEFAULT_ORGANIZATION_STRUCTURE

User = get_user_model()

class AccountsModelsTest(TestCase):
    def test_create_organization_models(self):
        company = Company.objects.create(name='Acme Corp')
        department = Department.objects.create(company=company, name='Legal')
        unit = Unit.objects.create(department=department, name='Contracts')
        user = User.objects.create_user(username='jdoe', password='password', role=User.ROLE_STAFF, company=company, department=department, unit=unit)

        self.assertEqual(str(company), 'Acme Corp')
        self.assertEqual(str(department), 'Acme Corp / Legal')
        self.assertEqual(str(unit), 'Legal / Contracts')
        self.assertEqual(user.department, department)
        self.assertEqual(user.unit, unit)

    def test_seed_organization_command_is_idempotent(self):
        stdout = StringIO()
        call_command('seed_organization', '--company-name', 'Main Company', stdout=stdout)
        call_command('seed_organization', '--company-name', 'Main Company', stdout=StringIO())

        company = Company.objects.get(name='Main Company')
        expected_departments = len(DEFAULT_ORGANIZATION_STRUCTURE)
        expected_units = sum(len(units) for units in DEFAULT_ORGANIZATION_STRUCTURE.values())

        self.assertEqual(Department.objects.filter(company=company).count(), expected_departments)
        self.assertEqual(Unit.objects.filter(department__company=company).count(), expected_units)
        self.assertTrue(Department.objects.filter(company=company, name='Finance').exists())
        self.assertTrue(Unit.objects.filter(department__company=company, department__name='Finance', name='Payroll').exists())
        self.assertIn('departments_created=10', stdout.getvalue())
