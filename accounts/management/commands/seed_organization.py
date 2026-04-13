from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Company, Department, Unit
from accounts.organization import DEFAULT_ORGANIZATION_STRUCTURE


class Command(BaseCommand):
    help = 'Create the default company departments and units.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-name',
            default='Main Company',
            help='Company name to seed. Defaults to "Main Company".',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        company_name = options['company_name'].strip() or 'Main Company'
        dry_run = options['dry_run']

        missing_departments, missing_units = self._missing_rows(company_name)
        if dry_run:
            self.stdout.write(f'Company: {company_name}')
            self.stdout.write(f'Missing departments: {len(missing_departments)}')
            self.stdout.write(f'Missing units: {len(missing_units)}')
            return

        with transaction.atomic():
            company, company_created = Company.objects.get_or_create(name=company_name)
            departments_created = 0
            units_created = 0

            for department_name, unit_names in DEFAULT_ORGANIZATION_STRUCTURE.items():
                department, created = Department.objects.get_or_create(
                    company=company,
                    name=department_name,
                )
                departments_created += int(created)

                for unit_name in unit_names:
                    _, created = Unit.objects.get_or_create(
                        department=department,
                        name=unit_name,
                    )
                    units_created += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                'Organization seeded. '
                f'company_created={int(company_created)} '
                f'departments_created={departments_created} '
                f'units_created={units_created}'
            )
        )

    def _missing_rows(self, company_name):
        try:
            company = Company.objects.get(name=company_name)
        except Company.DoesNotExist:
            unit_count = sum(len(units) for units in DEFAULT_ORGANIZATION_STRUCTURE.values())
            return list(DEFAULT_ORGANIZATION_STRUCTURE), [None] * unit_count

        existing_departments = set(
            Department.objects.filter(company=company).values_list('name', flat=True)
        )
        missing_departments = [
            name for name in DEFAULT_ORGANIZATION_STRUCTURE if name not in existing_departments
        ]

        missing_units = []
        departments = {
            department.name: department
            for department in Department.objects.filter(company=company)
        }
        for department_name, unit_names in DEFAULT_ORGANIZATION_STRUCTURE.items():
            department = departments.get(department_name)
            if not department:
                missing_units.extend((department_name, unit_name) for unit_name in unit_names)
                continue

            existing_units = set(department.units.values_list('name', flat=True))
            missing_units.extend(
                (department_name, unit_name)
                for unit_name in unit_names
                if unit_name not in existing_units
            )

        return missing_departments, missing_units
