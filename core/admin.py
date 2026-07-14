from django.contrib import admin

from .models import ShortLink, Subscription, LinkReport


@admin.register(ShortLink)
class ShortLinkAdmin(admin.ModelAdmin):
    list_display = ('short_code', 'owner', 'original_url', 'is_premium', 'clicks', 'creado', 'ultimo_click')
    list_filter = ('is_premium', 'creado')
    search_fields = ('short_code', 'original_url', 'owner__username')
    readonly_fields = ('creado', 'actualizado', 'clicks', 'ultimo_click')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'provider', 'fecha_inicio', 'fecha_fin', 'creado')
    list_filter = ('status', 'provider')
    search_fields = ('user__username', 'provider_id')


@admin.register(LinkReport)
class LinkReportAdmin(admin.ModelAdmin):
    list_display = ('link', 'reason', 'reporter', 'reporter_ip', 'creado', 'reviewed')
    list_filter = ('reason', 'reviewed', 'creado')
    search_fields = ('link__short_code', 'detail')
    readonly_fields = ('creado', 'reporter_ip')
    actions = ['mark_reviewed']

    @admin.action(description='Marcar como revisado')
    def mark_reviewed(self, request, queryset):
        queryset.update(reviewed=True)
