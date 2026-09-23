SECRET_KEY = "fixture-only"
DEBUG = True
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
USE_TZ = True
