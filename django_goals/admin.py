import json

from django.contrib import admin, messages
from django.db import models
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django_object_actions import DjangoObjectActions, action

from .models import Goal, GoalDependency, GoalProgress


class GoalDependencyInline(admin.TabularInline):
    model = GoalDependency
    fk_name = 'dependent_goal'
    extra = 0
    fields = (
        'precondition_goal',
        'precondition_goal__state',
        'precondition_goal__handler',
        'precondition_goal__created_at',
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Precondition Goal State')
    def precondition_goal__state(self, obj):
        return obj.precondition_goal.get_state_display()

    @admin.display(description='Precondition Goal Handler')
    def precondition_goal__handler(self, obj):
        return obj.precondition_goal.handler

    @admin.display(description='Precondition Goal Created At')
    def precondition_goal__created_at(self, obj):
        return obj.precondition_goal.created_at


class GoalProgressInline(admin.TabularInline):
    model = GoalProgress
    extra = 0

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Goal)
class GoalAdmin(DjangoObjectActions, admin.ModelAdmin):
    list_display = (
        'id', 'state', 'handler', 'precondition_date', 'created_at',
        'progress_count',
    )
    list_filter = ('state', 'precondition_date')
    search_fields = ('id',)

    fields = (
        'id',
        'state',
        'handler',
        'instructions_pre',
        'precondition_date',
        'deadline',
        'created_at',
        'related_objects',
    )
    inlines = (
        GoalDependencyInline,
        GoalProgressInline,
    )
    change_actions = (
        'retry',
        'block',
        'unblock',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            progress_count=models.Count('progress'),
        )

    @admin.display
    def progress_count(self, obj):
        return obj.progress_count

    @admin.display(description='Instructions')
    def instructions_pre(self, obj):
        return format_html(
            '<pre style="white-space: pre-wrap;">{}</pre>',
            json.dumps(obj.instructions, indent=2),
        )

    @admin.display(description='Related Objects')
    def related_objects(self, obj):
        related_objects = []
        for field in obj._meta._relation_tree:
            if field.model._meta.app_label == 'django_goals':
                continue
            related_objects.extend(field.model.objects.filter(**{field.name: obj}))
        rows_html = []
        for related_object in related_objects:
            row_html = format_html(
                (
                    '<tr>'
                    '<td>{related_app}</td>'
                    '<td>{related_model}</td>'
                    '<td>{related_object}</td>'
                    '</tr>'
                ),
                related_app=related_object._meta.app_label,
                related_model=related_object._meta.verbose_name,
                related_object=format_html(
                    '<a href="{related_object_url}">{related_object}</a>',
                    related_object_url=admin.site.url + related_object.get_admin_url(),
                    related_object=related_object,
                ),
            )
            rows_html.append(row_html)
        return format_html(
            '<table><tbody>{}</tbody></table>',
            ''.join(rows_html),
        )

    @action(label=_('Retry'), methods=['POST'], button_type='form')
    def retry(self, request, obj):
        try:
            obj.retry()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Goal was retried'))

    @action(label=_('Block'), methods=['POST'], button_type='form')
    def block(self, request, obj):
        try:
            obj.block()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Goal was blocked'))

    @action(label=_('Unblock'), methods=['POST'], button_type='form')
    def unblock(self, request, obj):
        try:
            obj.unblock()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Goal was unblocked'))
