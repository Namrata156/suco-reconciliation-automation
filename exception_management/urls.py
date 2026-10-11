from django.urls import path

from exception_management import api

urlpatterns = [
    path("cases/", api.CaseListView.as_view(), name="exception-case-list"),
    path("cases/bulk/<str:name>/", api.BulkView.as_view(), name="exception-case-bulk"),
    path("cases/<int:pk>/", api.CaseDetailView.as_view(), name="exception-case-detail"),
    path("cases/<int:pk>/<str:name>/", api.CaseTransitionView.as_view(), name="exception-case-transition"),
]
