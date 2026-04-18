from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.generic import TemplateView

from .forms import DepartmentCreateForm, UnitCreateForm
from .models import Company, Department, Unit
from .organization import DEFAULT_COMPANY_NAME, missing_organization_rows, normalized_company_name, seed_organization

@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html', {'user': request.user})


class OrganizationSettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/organization_settings.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not (request.user.is_superuser or request.user.is_admin()):
            raise PermissionDenied('You do not have permission to manage organization settings.')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        company = self._target_company(create=True)
        action = request.POST.get('action')

        if action == 'seed':
            result = seed_organization(company.name)
            messages.success(
                request,
                (
                    f'Organization settings ready for {company.name}. '
                    f'Departments added: {result["departments_created"]}. '
                    f'Units added: {result["units_created"]}.'
                ),
            )
            return redirect('accounts:organization_settings')

        if action == 'add_department':
            department_form = DepartmentCreateForm(request.POST, company=company)
            unit_form = UnitCreateForm(company=company)
            if department_form.is_valid():
                department = department_form.save()
                messages.success(request, f'Department "{department.name}" added.')
                return redirect('accounts:organization_settings')
            return self.render_to_response(
                self.get_context_data(
                    department_form=department_form,
                    unit_form=unit_form,
                )
            )

        if action == 'add_unit':
            department_form = DepartmentCreateForm(company=company)
            unit_form = UnitCreateForm(request.POST, company=company)
            if unit_form.is_valid():
                unit = unit_form.save()
                messages.success(
                    request,
                    f'Unit "{unit.name}" added to {unit.department.name}.',
                )
                return redirect('accounts:organization_settings')
            return self.render_to_response(
                self.get_context_data(
                    department_form=department_form,
                    unit_form=unit_form,
                )
            )

        messages.error(request, 'Unknown organization action.')
        return redirect('accounts:organization_settings')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self._target_company()
        company_name = self._target_company_name()
        missing_departments, missing_units = missing_organization_rows(company_name)

        context['organization_company_name'] = company_name
        context['organization_counts'] = {
            'departments': Department.objects.filter(company__name=company_name).count(),
            'units': Unit.objects.filter(department__company__name=company_name).count(),
        }
        context['organization_setup_needed'] = bool(missing_departments or missing_units)
        context['organization_missing_departments'] = len(missing_departments)
        context['organization_missing_units'] = len(missing_units)
        context['departments'] = (
            Department.objects.filter(company__name=company_name)
            .prefetch_related('units')
            .order_by('name')
        )
        context['department_form'] = kwargs.get('department_form') or DepartmentCreateForm(company=company)
        context['unit_form'] = kwargs.get('unit_form') or UnitCreateForm(company=company)
        return context

    def _target_company_name(self):
        name = self.request.user.company.name if self.request.user.company_id else DEFAULT_COMPANY_NAME
        return normalized_company_name(name)

    def _target_company(self, *, create=False):
        company_name = self._target_company_name()
        queryset = Company.objects.filter(name=company_name)
        if create:
            company, _ = Company.objects.get_or_create(name=company_name)
            return company
        return queryset.first()
