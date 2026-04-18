from django.core.management.base import BaseCommand

from accounts.organization import DEFAULT_COMPANY_NAME, missing_organization_rows, normalized_company_name, seed_organization


class Command(BaseCommand):
    help = 'Create the default company departments and units.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-name',
            default=DEFAULT_COMPANY_NAME,
            help=f'Company name to seed. Defaults to "{DEFAULT_COMPANY_NAME}".',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        company_name = normalized_company_name(options['company_name'])
        dry_run = options['dry_run']

        missing_departments, missing_units = missing_organization_rows(company_name)
        if dry_run:
            self.stdout.write(f'Company: {company_name}')
            self.stdout.write(f'Missing departments: {len(missing_departments)}')
            self.stdout.write(f'Missing units: {len(missing_units)}')
            return

        result = seed_organization(company_name)

        self.stdout.write(
            self.style.SUCCESS(
                'Organization seeded. '
                f'company_created={result["company_created"]} '
                f'departments_created={result["departments_created"]} '
                f'units_created={result["units_created"]}'
            )
        )
