from django.urls import path

from .views import bootstrap_view, me_view, profile_detail_view, profile_me_view

urlpatterns = [
    # POST /api/auth/bootstrap/ - create or sync the local user after Clerk login.
    path('bootstrap/', bootstrap_view, name='auth-bootstrap'),
    # GET /api/auth/me/ - return the current authenticated user and profile summary.
    path('me/', me_view, name='auth-me'),
    # GET/PATCH /api/auth/profile/me/ - read or update the current user's profile.
    path('profile/me/', profile_me_view, name='profile-me'),
    # GET /api/auth/profiles/<username>/ - load a public profile by username.
    path('profiles/<str:username>/', profile_detail_view, name='profile-detail'),
]
