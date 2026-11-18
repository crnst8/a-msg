import os


def get_scoped_secret(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
