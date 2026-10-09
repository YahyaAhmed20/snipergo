
from django.conf import settings
from django.core.files.storage import FileSystemStorage


class CaptainDocumentStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "location",
            settings.BASE_DIR / "private_media" / "captain_documents",
        )
        kwargs.setdefault("base_url", "/private-documents/")
        super().__init__(*args, **kwargs)
