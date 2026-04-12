from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Company, Department, Unit

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
