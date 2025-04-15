import json

from django.contrib import admin, messages
from django.db import models
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _
from django_object_actions import DjangoObjectActions, action

from .models import (
    Goal, GoalDependency, GoalProgress, block_goal, unblock_retry_goal,
)


class GoalDependencyInline(admin.TabularInline):
    model = GoalDependency
    fk_name = 'dependent_goal'
    extra = 0
    fields = (
        'precondition_goal__id',
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

    @admin.display(description='Precondition Goal ID')
    def precondition_goal__id(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse(
                'admin:django_goals_goal_change',
                args=(obj.precondition_goal.id,),
            ),
            obj.precondition_goal.id,
        )

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
        'id', 'state', 'handler',
        'precondition_date',
        'waiting_for_not_achieved_count',
        'created_at',
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
        'preconditions_mode',
        'precondition_failure_behavior',
        'waiting_for_count',
        'waiting_for_not_achieved_count',
        'waiting_for_failed_count',
        'deadline',
        'created_at',
        'related_objects',
    )
    inlines = (
        GoalDependencyInline,
        GoalProgressInline,
    )
    change_actions = (
        'block',
        'unblock_retry',
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
        rows_html = []
        for field in obj._meta._relation_tree:
            if field.model._meta.app_label == 'django_goals':
                continue
            related_objects = field.model.objects.filter(**{field.name: obj})
            for related_object in related_objects:
                try:
                    object_admin_url = reverse(
                        f'admin:{related_object._meta.app_label}_{related_object._meta.model_name}_change',
                        args=(related_object.pk,),
                    )
                except NoReverseMatch:
                    object_link = format_html(
                        '<span>{related_object}</span>',
                        related_object=related_object,
                    )
                else:
                    object_link = format_html(
                        '<a href="{object_admin_url}">{related_object}</a>',
                        object_admin_url=object_admin_url,
                        related_object=related_object,
                    )
                row_html = format_html(
                    (
                        '<tr>'
                        '<td>{related_app}</td>'
                        '<td>{related_model}</td>'
                        '<td>{related_field}</td>'
                        '<td>{related_object}</td>'
                        '</tr>'
                    ),
                    related_app=related_object._meta.app_label,
                    related_model=related_object._meta.verbose_name,
                    related_field=field.verbose_name,
                    related_object=object_link,
                )
                rows_html.append(row_html)
        return format_html_join(
            '',
            (
                '<table>'
                '<thead><tr>'
                '<th>App</th>'
                '<th>Model</th>'
                '<th>Field</th>'
                '<th>Object</th>'
                '</tr></thead>'
                '<tbody>{}</tbody>'
                '</table>'
            ),
            ((row,) for row in rows_html),
        )

    @action(label=_('Unblock / retry'), methods=['POST'], button_type='form')
    def unblock_retry(self, request, obj):
        try:
            unblock_retry_goal(obj.id)
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Goal was unblocked'))

    @action(label=_('Block'), methods=['POST'], button_type='form')
    def block(self, request, obj):
        try:
            block_goal(obj.id)
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Goal was blocked'))
