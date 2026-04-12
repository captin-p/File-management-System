from django.urls import path
from . import views

app_name = 'documents'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload_document, name='upload_document'),
    path('browse/', views.browse_documents, name='browse_documents'),
    path('browse/<slug:department_slug>/', views.browse_documents, name='browse_department'),
    path('browse/<slug:department_slug>/<int:year>/', views.browse_documents, name='browse_folder_year'),
    path('browse/<slug:department_slug>/<int:year>/<str:document_type>/', views.browse_documents, name='browse_folder_type'),
    path('documents/', views.document_list, name='document_list'),
    path('documents/<int:pk>/', views.document_detail, name='document_detail'),
    path('documents/<int:pk>/edit/', views.edit_document, name='edit_document'),
    path('documents/<int:pk>/delete/', views.delete_document, name='delete_document'),
    path('api/documents/', views.document_api_list, name='document_api_list'),
    path('api/documents/<int:pk>/', views.document_api_detail, name='document_api_detail'),
]
