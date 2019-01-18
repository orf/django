from pathlib import Path

from django.dispatch import receiver
from django.template import engines
from django.template.backends.django import DjangoTemplates
from django.template.loaders.cached import Loader as CachedLoader
from django.template.loaders.filesystem import Loader as FilesystemLoader
from django.utils.autoreload import autoreload_started, file_changed


def get_cached_loaders():
    return (
        loader
        for engine in engines.all()
        if isinstance(engine, DjangoTemplates)
        for loader in engine.engine.template_loaders
        if isinstance(loader, CachedLoader)
    )


@receiver(autoreload_started, dispatch_uid='reset_cached_templates')
def watch_for_template_changes(sender, **kwargs):
    cached_sub_loaders = (
        sub_loader
        for loader in get_cached_loaders()
        for sub_loader in loader.loaders
        if isinstance(sub_loader, FilesystemLoader)
    )
    for loader in cached_sub_loaders:
        for directory in loader.get_dirs():
            sender.watch_dir(Path(directory).absolute(), '**/*')


@receiver(file_changed, dispatch_uid='template_file_changed')
def template_file_changed(sender, file_path, **kwargs):
    for loader in get_cached_loaders():
        loader.reset()
    return True
