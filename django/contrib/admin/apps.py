from django.apps import AppConfig
from django.core import checks
from django.utils.translation import gettext_lazy as _

from django.contrib.admin.checks import check_admin_app, check_dependencies


class SimpleAdminConfig(AppConfig):
    """Simple AppConfig which does not do automatic discovery."""

    default_auto_field = 'django.db.models.AutoField'
    default_site = 'django.contrib.admin.sites.AdminSite'
    name = 'django.contrib.admin'
    verbose_name = _("Administration this is a really long line that I hope will trigger a flake8 failure and have a nice github-actions output in the PR")

    def ready(self):
        checks.register(check_dependencies, checks.Tags.admin)
        checks.register(check_admin_app, checks.Tags.admin)


class AdminConfig(SimpleAdminConfig):
    """The default AppConfig for admin which does autodiscovery."""

    default = True

    def ready(self):
        super().ready()
        self.module.autodiscover()
