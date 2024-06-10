import json

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django_object_actions import DjangoObjectActions, action

from .models import Task, TaskDependency, TaskExecution


class TaskDependencyInline(admin.TabularInline):
    model = TaskDependency
    fk_name = 'dependent_task'
    extra = 0
    fields = (
        'precondition_task',
        'precondition_task__state',
        'precondition_task__type',
        'precondition_task__created_at',
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Precondition Task State')
    def precondition_task__state(self, obj):
        return obj.precondition_task.get_state_display()

    @admin.display(description='Precondition Task Type')
    def precondition_task__type(self, obj):
        return obj.precondition_task.task_type

    @admin.display(description='Precondition Task Created At')
    def precondition_task__created_at(self, obj):
        return obj.precondition_task.created_at


class TaskExecutionInline(admin.TabularInline):
    model = TaskExecution
    extra = 0

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Task)
class TaskAdmin(DjangoObjectActions, admin.ModelAdmin):
    list_display = ('id', 'state', 'task_type', 'precondition_date', 'created_at')
    list_filter = ('state', 'precondition_date')
    search_fields = ('id',)

    fields = (
        'id',
        'state',
        'task_type',
        'instructions_pre',
        'precondition_date',
        'created_at',
    )
    inlines = [TaskDependencyInline, TaskExecutionInline]
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

    @admin.display(description='Instructions')
    def instructions_pre(self, obj):
        return format_html(
            '<pre style="white-space: pre-wrap;">{}</pre>',
            json.dumps(obj.instructions, indent=2),
        )

    @action(label=_('Retry'), methods=['POST'], button_type='form')
    def retry(self, request, obj):
        try:
            obj.retry()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)

    @action(label=_('Block'), methods=['POST'], button_type='form')
    def block(self, request, obj):
        try:
            obj.block()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Task was blocked'))

    @action(label=_('Unblock'), methods=['POST'], button_type='form')
    def unblock(self, request, obj):
        try:
            obj.unblock()
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
        else:
            self.message_user(request, _('Task was unblocked'))
