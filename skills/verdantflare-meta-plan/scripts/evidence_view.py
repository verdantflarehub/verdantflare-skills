"""Read-only logical Markdown view for validated archive relocation.

No system API is patched. Only callers of these helpers see a scoped view;
native sources, policies, tools and ordinary files remain live filesystem reads.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType

_view = ContextVar('validated_archive_markdown', default=None)


def _key(path: Path) -> Path:
    return path.resolve()


def original(path: Path) -> bytes | None:
    current = _view.get()
    return None if current is None else current.get(_key(path))


def read_bytes(path: Path, limit: int | None = None) -> bytes:
    value = original(path)
    if value is not None:
        return value if limit is None else value[:limit]
    with path.open('rb') as stream:
        return stream.read() if limit is None else stream.read(limit)


def read_text(path: Path) -> str:
    return read_bytes(path).decode('utf-8')


def is_file(path: Path) -> bool:
    return original(path) is not None or path.is_file()


def resolve_file(path: Path) -> Path:
    return path.resolve() if original(path) is not None else path.resolve(strict=True)


def glob_markdown(folder: Path) -> list[Path]:
    current = _view.get() or {}
    return sorted(set(folder.glob('*.md')) | {p for p in current if p.parent == folder.resolve()})


@contextmanager
def validated_markdown(originals: dict[Path, bytes]):
    """Internal scope entered only after the archive consumer validates originals."""
    if _view.get() is not None:
        raise ValueError('Nested archive views are not supported')
    resolved = {_key(p): value for p, value in originals.items()}
    if len(resolved) != len(originals) or any(p.suffix != '.md' or not isinstance(v, bytes)
                                            for p, v in resolved.items()):
        raise ValueError('Archive views accept distinct Markdown sources only')
    token = _view.set(MappingProxyType(resolved))
    try:
        yield
    finally:
        _view.reset(token)
