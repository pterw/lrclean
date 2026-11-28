"""Shared path helpers: normalisation, long-path access, file kinds."""

import unicodedata

RAW_EXTS = frozenset("arw cr2 cr3 dng nef nrw orf pef raf rw2 srw".split())
JPEG_EXTS = frozenset({"jpg", "jpeg"})
PHOTO_EXTS = RAW_EXTS | JPEG_EXTS
SIDECAR_EXTS = frozenset({"xmp", "acr"})
PLACEHOLDER_MASK = 0x1000 | 0x40000 | 0x400000
LONG_LIMIT = 247
NAME_LIMIT = 255

_EXT_PREFIX = "\\\\?\\"
_EXT_UNC_PREFIX = "\\\\?\\UNC\\"


def _strip_prefix(path: str) -> str:
    if path.startswith(_EXT_UNC_PREFIX):
        return "\\\\" + path[len(_EXT_UNC_PREFIX):]
    if path.startswith(_EXT_PREFIX):
        return path[len(_EXT_PREFIX):]
    return path


def norm(path: str) -> str:
    if not path:
        return ""
    text = _strip_prefix(path).replace("\\", "/")
    text = unicodedata.normalize("NFC", text).lower()
    text = unicodedata.normalize("NFC", text)
    is_drive_root = len(text) == 3 and text[1] == ":" and text[2] == "/"
    if not is_drive_root:
        text = text.rstrip("/") or "/"
    return text


def fs_path(path: str) -> str:
    if len(path) <= LONG_LIMIT or path.startswith(_EXT_PREFIX):
        return path
    if path.startswith(("\\\\", "//")):
        return _EXT_UNC_PREFIX + path[2:].replace("/", "\\")
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return _EXT_PREFIX + path.replace("/", "\\")
    return path


def name_units(name: str) -> int:
    """Measure a Windows name component in UTF-16 code units."""
    return len(name.encode("utf-16-le", errors="surrogatepass")) // 2


def signed64(n: int) -> int:
    return n if n < 2**63 else n - 2**64


def is_placeholder(attrs: int) -> bool:
    return bool(attrs & PLACEHOLDER_MASK)


def volume(path: str) -> str:
    text = _strip_prefix(path)
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return text[:2].lower()
    return ""


def split_path(path: str) -> tuple[str, str]:
    cut = max(path.rfind("/"), path.rfind("\\"))
    if cut < 0:
        return "", path
    return path[:cut], path[cut + 1:]


def make_container(kind: str, folder_norm: str) -> str:
    return f"{kind}:{folder_norm}"


def split_container(value: str) -> tuple[str, str] | None:
    if not value:
        return None
    kind, _, folder = value.partition(":")
    return kind, folder
