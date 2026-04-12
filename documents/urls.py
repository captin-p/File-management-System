from django.urls import path
from . import views

app_name = 'documents'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('upload/', views.DocumentUploadView.as_view(), name='upload_document'),
    path('browse/', views.DocumentBrowseView.as_view(), name='browse_documents'),
    path('browse/<slug:department_slug>/', views.DocumentBrowseView.as_view(), name='browse_department'),
    path('browse/<slug:department_slug>/<int:year>/', views.DocumentBrowseView.as_view(), name='browse_folder_year'),
    path('browse/<slug:department_slug>/<int:year>/<str:document_type>/', views.DocumentBrowseView.as_view(), name='browse_folder_type'),
    path('documents/', views.DocumentListView.as_view(), name='document_list'),
    path('api/documents/', views.DocumentApiListView.as_view(), name='document_api_list'),
    path('api/documents/<uuid:pk>/', views.DocumentApiDetailView.as_view(), name='document_api_detail'),
    path('documents/<uuid:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('documents/<uuid:pk>/edit/', views.DocumentUpdateView.as_view(), name='edit_document'),
    path('documents/<uuid:pk>/delete/', views.DocumentDeleteView.as_view(), name='delete_document'),
]
