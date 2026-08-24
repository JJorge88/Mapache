from django.core.files.base import ContentFile
from django.core.files.storage import Storage


class NoPathMemoryStorage(Storage):
    def __init__(self, **kwargs):
        self.files = {}
        self.url_calls = []

    def _open(self, name, mode="rb"):
        if name not in self.files:
            raise FileNotFoundError(name)
        return ContentFile(self.files[name], name=name)

    def _save(self, name, content):
        content.seek(0)
        self.files[name] = content.read()
        return name

    def delete(self, name):
        self.files.pop(name, None)

    def exists(self, name):
        return name in self.files

    def size(self, name):
        return len(self.files[name])

    def url(self, name, expire=None, parameters=None):
        self.url_calls.append((name, expire, parameters))
        suffix = f"?expires={expire}" if expire is not None else ""
        return f"https://storage.test/{name}{suffix}"

    def path(self, name):
        raise NotImplementedError("Este storage no dispone de paths locales.")
