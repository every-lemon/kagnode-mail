"""Morrow Mail. Keep CLI and milter startup free of web-framework imports."""


def create_app(overrides=None):
    from .web import create_app as factory

    return factory(overrides)
