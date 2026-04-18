from django.db import transaction

from .models import Company, Department, Unit


DEFAULT_COMPANY_NAME = 'Main Company'


DEFAULT_ORGANIZATION_STRUCTURE = {
    'Administration': [
        'Executive Office',
        'Records Management',
        'Facilities',
        'Front Desk',
    ],
    'Finance': [
        'Accounts Payable',
        'Accounts Receivable',
        'Payroll',
        'Budgeting',
        'Audit',
    ],
    'Human Resources': [
        'Recruitment',
        'Employee Relations',
        'Training',
        'Benefits',
    ],
    'Legal': [
        'Contracts',
        'Compliance',
        'Litigation',
        'Corporate Governance',
    ],
    'Information Technology': [
        'Infrastructure',
        'Help Desk',
        'Security',
        'Software Systems',
    ],
    'Operations': [
        'Planning',
        'Quality Assurance',
        'Logistics',
        'Field Operations',
    ],
    'Procurement': [
        'Purchasing',
        'Vendor Management',
        'Inventory',
    ],
    'Sales': [
        'Business Development',
        'Account Management',
        'Tender Management',
    ],
    'Customer Service': [
        'Support Desk',
        'Client Relations',
        'Complaints',
    ],
    'Records and Archives': [
        'Intake',
        'Indexing',
        'Retention',
        'Retrieval',
    ],
}


def normalized_company_name(company_name):
    return (company_name or '').strip() or DEFAULT_COMPANY_NAME


def missing_organization_rows(company_name=DEFAULT_COMPANY_NAME):
    company_name = normalized_company_name(company_name)

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


@transaction.atomic
def seed_organization(company_name=DEFAULT_COMPANY_NAME):
    company_name = normalized_company_name(company_name)
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

    return {
        'company': company,
        'company_created': int(company_created),
        'departments_created': departments_created,
        'units_created': units_created,
    }
