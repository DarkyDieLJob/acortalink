"""Root URL configuration for acortador standalone."""

from django.contrib.auth import views as auth_views
from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('s/<code>/reportar/', core_views.reportar_link, name='reportar'),
    path('s/<code>/', core_views.redirect_view, name='redirect'),
    path('', include('core.urls')),
]
