from django.urls import path
from . import views

app_name = 'documents'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('upload/', views.DocumentUploadView.as_view(), name='upload_document'),
    path('documents/', views.DocumentListView.as_view(), name='document_list'),
    path('documents/<uuid:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('documents/<uuid:pk>/edit/', views.DocumentUpdateView.as_view(), name='edit_document'),
    path('documents/<uuid:pk>/delete/', views.DocumentDeleteView.as_view(), name='delete_document'),
]
