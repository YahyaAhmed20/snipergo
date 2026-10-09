
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse


def home(request):
    return HttpResponse(
        """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>SNIPER GO</title>
            <style>
                body {
                    margin: 0;
                    min-height: 100vh;
                    display: grid;
                    place-items: center;
                    background: #101820;
                    color: white;
                    font-family: Arial, sans-serif;
                    text-align: center;
                }
                h1 { font-size: 42px; letter-spacing: 4px; }
                p { color: #aab4bf; }
                a { color: #7ee787; text-decoration: none; }
            </style>
        </head>
        <body>
            <main>
                <h1>SNIPER GO</h1>
                <p>Smart Mobility. Moving Forward.</p>
                <p>Platform is running successfully.</p>
                <a href="/admin/">Administration</a>
            </main>
        </body>
        </html>
        """,
        content_type="text/html; charset=utf-8",
    )


urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.STATIC_ROOT,
    )
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
