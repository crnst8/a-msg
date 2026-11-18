import os


def get_session_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
