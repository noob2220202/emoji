"""여러 모듈에서 공용으로 쓰는 ffmpeg 실행 파일 탐색 로직."""

import shutil


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()
