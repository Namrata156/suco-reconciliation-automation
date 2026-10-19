"""
/api/v1/ (Phase 5 design 3.1). Not module-specific first, then
/api/v1/<module>/... where <module> is PaymentModule.code in lower case.
"""
from django.urls import path

from api import auth, operations, views

urlpatterns = [
    path("auth/csrf/", auth.CsrfView.as_view(), name="v1-auth-csrf"),
    path("auth/login/", auth.LoginView.as_view(), name="v1-auth-login"),
    path("auth/logout/", auth.LogoutView.as_view(), name="v1-auth-logout"),
    path("me/", auth.MeView.as_view(), name="v1-me"),
    path("modules/", views.ModuleListView.as_view(), name="v1-modules"),
    path("users/", views.UserListView.as_view(), name="v1-users"),
    # per module
    path("<slug:module>/meta/", views.MetaView.as_view(), name="v1-meta"),
    path("<slug:module>/summary/", views.SummaryView.as_view(), name="v1-summary"),
    path("<slug:module>/cases/", views.CaseListView.as_view(), name="v1-case-list"),
    path("<slug:module>/cases/bulk/<str:name>/", views.BulkView.as_view(), name="v1-case-bulk"),
    path("<slug:module>/cases/<int:pk>/", views.CaseDetailView.as_view(), name="v1-case-detail"),
    path("<slug:module>/cases/<int:pk>/history/", views.CaseHistoryView.as_view(), name="v1-case-history"),
    path("<slug:module>/cases/<int:pk>/<str:name>/", views.CaseTransitionView.as_view(), name="v1-case-transition"),
    # files, CBS date, business dates and the run (Slice 2)
    path("<slug:module>/files/", operations.FileListView.as_view(), name="v1-file-list"),
    path("<slug:module>/files/detect/", operations.FileDetectView.as_view(), name="v1-file-detect"),
    path("<slug:module>/files/<int:pk>/", operations.FileDetailView.as_view(), name="v1-file-detail"),
    path("<slug:module>/files/<int:pk>/exceptions/", operations.FileExceptionsView.as_view(),
         name="v1-file-exceptions"),
    path("<slug:module>/files/<int:pk>/select-date/", operations.FileSelectDateView.as_view(),
         name="v1-file-select-date"),
    path("<slug:module>/dates/", operations.DateListView.as_view(), name="v1-date-list"),
    path("<slug:module>/dates/<str:business_date>/", operations.DateDetailView.as_view(), name="v1-date-detail"),
    path("<slug:module>/dates/<str:business_date>/run/", operations.DateRunView.as_view(), name="v1-date-run"),
]
