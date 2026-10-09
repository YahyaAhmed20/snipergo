
import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class CaptainDocumentStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        private_root = Path(
            os.environ.get(
                "PRIVATE_MEDIA_ROOT",
                str(settings.BASE_DIR / "private_media"),
            )
        )

        storage_location = private_root / "captain_documents"

        kwargs.setdefault("location", storage_location)
        kwargs.setdefault("base_url", None)

        super().__init__(*args, **kwargs)
