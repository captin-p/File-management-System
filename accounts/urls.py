from django.contrib.auth import views as auth_views
from django.urls import path
from .views import OrganizationSettingsView, profile_view

app_name = 'accounts'

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='accounts:login'), name='logout'),
    path('profile/', profile_view, name='profile'),
    path('organization/', OrganizationSettingsView.as_view(), name='organization_settings'),
]
