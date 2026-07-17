from django.urls import path, include

from . import views
from .api import api_urls
from .internal_api import internal_urls

app_name = 'core'

urlpatterns = [
    path('', views.index, name='index'),
    path('mis-links/', views.mis_links, name='mis_links'),
    path('mis-links/export/', views.export_analytics, name='export_analytics'),
    path('bulk/', views.bulk, name='bulk'),
    path('bulk/plantilla/', views.plantilla_csv, name='plantilla'),
    path('editar/<int:pk>/', views.editar_link, name='editar_link'),
    path('eliminar/<int:pk>/', views.eliminar_link, name='eliminar'),
    path('qr/<str:code>/', views.qr_code, name='qr_code'),
    path('registrar/', views.registro, name='registro'),
    path('activar/<str:token>/', views.activar_cuenta, name='activar'),
    path('ingresar/', views.login_acortador, name='login'),
    path('perfil/', views.perfil, name='perfil'),
    path('verificar/', views.verificar_accion, name='verificar'),
    path('subscribir/', views.subscribir, name='subscribir'),
    path('subscribir/checkout/', views.checkout, name='checkout'),
    path('subscribir/cancelar/', views.cancelar_subscripcion, name='cancelar_sub'),
    path('stripe/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('mercadopago/webhook/', views.mercadopago_webhook, name='mp_webhook'),
    path('privacidad/', views.privacidad, name='privacidad'),
    path('contacto/', views.contacto_acortador, name='contacto'),
    path('dominios/', views.custom_domains, name='custom_domains'),
    path('team/', views.team_members, name='team_members'),
    path('webhooks/', views.webhooks, name='webhooks'),
    path('api-keys/', views.api_keys, name='api_keys'),
    path('api/v1/', include(api_urls)),
    path('api/internal/', include(internal_urls)),
]
