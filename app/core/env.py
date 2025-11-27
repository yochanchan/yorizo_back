import os


def get_app_env() -> str:
    """Return current APP_ENV value or empty string when unset."""
    return os.getenv("APP_ENV", "") or ""


def is_test_env() -> bool:
    """True when running in test mode (APP_ENV=test)."""
    return get_app_env().lower() == "test"
