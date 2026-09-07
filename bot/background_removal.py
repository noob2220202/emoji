"""이미지의 배경을 제거(누끼)한다.

[rembg](https://github.com/danielgatis/rembg)(U^2-Net 기반 오픈소스 배경 제거
라이브러리)를 사용해 PNG 바이트를 받아 배경이 제거된 PNG(RGBA) 바이트를 반환한다.
"""

from rembg import new_session, remove

from . import config

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = new_session(config.REMBG_MODEL)
    return _session


def remove_background_image(data: bytes) -> bytes:
    """이미지 바이트를 받아 배경이 제거된 PNG(RGBA) 바이트를 반환한다."""
    return remove(data, session=_get_session())
