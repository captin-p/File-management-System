import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-this-secret-key')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'accounts',
    'documents',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'dms_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'dms_project.wsgi.application'

# Use PostgreSQL by default in production.
# SQLite is supported only as a local development/test fallback
# when DJANGO_USE_SQLITE=True is explicitly set.
USE_SQLITE = os.environ.get('DJANGO_USE_SQLITE', 'False') == 'True'

if USE_SQLITE:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'dms'),
            'USER': os.environ.get('POSTGRES_USER', 'dmsuser'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'changeme'),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_TESSERACT_CMD = Path('C:/Program Files/Tesseract-OCR/tesseract.exe')
OCR_TESSERACT_CMD = os.environ.get('OCR_TESSERACT_CMD') or (
    str(DEFAULT_TESSERACT_CMD) if DEFAULT_TESSERACT_CMD.exists() else ''
)
DEFAULT_POPPLER_PATH = Path.home() / 'AppData/Local/Programs/Poppler/Library/bin'
OCR_POPPLER_PATH = os.environ.get('OCR_POPPLER_PATH') or (
    str(DEFAULT_POPPLER_PATH) if DEFAULT_POPPLER_PATH.exists() else ''
)
OCR_PDF_MAX_PAGES = max(1, int(os.environ.get('OCR_PDF_MAX_PAGES', '2')))

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)
CELERY_TASK_ALWAYS_EAGER = os.environ.get('CELERY_TASK_ALWAYS_EAGER', 'False') == 'True'
CELERY_TASK_EAGER_PROPAGATES = os.environ.get('CELERY_TASK_EAGER_PROPAGATES', 'True') == 'True'
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_PUBLISH_RETRY = os.environ.get('CELERY_TASK_PUBLISH_RETRY', 'False') == 'True'
CELERY_BROKER_CONNECTION_TIMEOUT = int(os.environ.get('CELERY_BROKER_CONNECTION_TIMEOUT', '3'))
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
AI_METADATA_ENABLED = os.environ.get('AI_METADATA_ENABLED', 'False') == 'True'
AI_METADATA_MODEL = os.environ.get('AI_METADATA_MODEL', 'gpt-4.1-mini')
AI_METADATA_USE_IMAGE = os.environ.get('AI_METADATA_USE_IMAGE', 'True') == 'True'
AI_METADATA_MAX_OCR_CHARS = int(os.environ.get('AI_METADATA_MAX_OCR_CHARS', '12000'))
AI_METADATA_MAX_HEADINGS = int(os.environ.get('AI_METADATA_MAX_HEADINGS', '5'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '[%(levelname)s] %(asctime)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
