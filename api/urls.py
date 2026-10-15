"""
/api/v1/ (Phase 5 design 3.1). Not module-specific first, then
/api/v1/<module>/... where <module> is PaymentModule.code in lower case.
"""
from django.urls import path

from api import auth, views

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
]
