"""문구를 받아 폰트를 입힌 메탈릭(크롬) 스타일 이미지를 만드는 "글자 이모지화" 기능.

정지 버전은 진한 배경 위에 세로 그라데이션이 들어간 금속 느낌 글자를 그린 PNG를 만들고,
움직이는 버전은 같은 글자를 고정한 채 뒤쪽 배경 그라데이션만 좌우로 흐르듯 움직이는
짧은 애니메이션 WEBM 배너를 만든다. 두 경우 모두 결과물은 이후 이미지/영상 분할
파이프라인에 그대로 들어가서, 사진(또는 업로드 영상)을 보냈을 때와 같은 방식으로
이모지 팩으로 쪼개진다.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import config
from .ffmpeg_util import ffmpeg_path

FONT_FILES: Dict[str, str] = {
    "black_han_sans": "BlackHanSans-Regular.ttf",
    "jua": "Jua-Regular.ttf",
    "do_hyeon": "DoHyeon-Regular.ttf",
    "nanum_pen": "NanumPenScript-Regular.ttf",
}

# 버튼에 보여줄 폰트 이름(사람이 읽기 쉬운 이름 + 느낌 설명)
FONTS: Dict[str, str] = {
    "black_han_sans": "블랙한산스 (임팩트)",
    "jua": "주아 (귀여움)",
    "do_hyeon": "도현 (심플)",
    "nanum_pen": "나눔펜 (손글씨)",
}


@dataclass(frozen=True)
class StylePreset:
    label: str
    text_colors: Tuple[str, ...]  # 세로 그라데이션(위->아래) - 금속 광택 느낌
    stroke_color: str
    wave_colors: Tuple[str, ...]  # 움직이는 버전에서 배경이 좌우로 흐를 때 도는 색(어두운 톤 권장)

    bg_colors: Tuple[str, str] = ("#141414", "#000000")  # 정지 버전 배경(세로 그라데이션)
    stroke_width: int = 5


# 레퍼런스 이미지(진한 배경 + 은색/금속 느낌 입체 글자)를 기준으로, 색상만 바꾼 프리셋들.
STYLES: Dict[str, StylePreset] = {
    "silver": StylePreset(
        label="⚪ 실버 크롬",
        text_colors=("#ffffff", "#d8d8d8", "#8a8a8a"),
        stroke_color="#000000",
        wave_colors=("#0a0a0a", "#3a3a3a", "#0a0a0a"),
    ),
    "gold": StylePreset(
        label="🟡 골드 크롬",
        text_colors=("#fff6c9", "#e8c04a", "#8a6a12"),
        stroke_color="#1a1305",
        wave_colors=("#0a0700", "#3a2a05", "#0a0700"),
    ),
    "blue": StylePreset(
        label="🔵 블루 크롬",
        text_colors=("#eaf6ff", "#5aa8e8", "#163a5c"),
        stroke_color="#020a12",
        wave_colors=("#00050a", "#052540", "#00050a"),
    ),
    "red": StylePreset(
        label="🔴 레드 크롬",
        text_colors=("#ffefe9", "#e85a4a", "#701c12"),
        stroke_color="#150402",
        wave_colors=("#0a0000", "#3a0808", "#0a0000"),
    ),
    "green": StylePreset(
        label="🟢 그린 크롬",
        text_colors=("#eaffef", "#4ac888", "#12502f"),
        stroke_color="#02150a",
        wave_colors=("#000a02", "#0a3a15", "#000a02"),
    ),
    "purple": StylePreset(
        label="🟣 퍼플 크롬",
        text_colors=("#f6eaff", "#a85ae8", "#4a1670"),
        stroke_color="#0d0215",
        wave_colors=("#08000a", "#2a0a3a", "#08000a"),
    ),
}

MAX_TEXT_WIDTH = 1400
PADDING_X = 90
PADDING_Y = 90
MAX_PHRASE_LEN = 30

ANIM_FPS = 15
ANIM_FRAMES = 45  # 15fps * 3s = 정확히 3초 루프


def _font_path(font_key: str) -> str:
    filename = FONT_FILES.get(font_key)
    if not filename:
        raise ValueError(f"알 수 없는 폰트: {font_key}")
    path = os.path.join(config.FONT_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"폰트 파일을 찾을 수 없습니다: {path}")
    return path


def _make_gradient(size: Tuple[int, int], colors: Tuple[str, ...], horizontal: bool) -> Image.Image:
    width, height = size
    if len(colors) == 1:
        return Image.new("RGB", size, colors[0])

    length = width if horizontal else height
    strip = Image.new("RGB", (length, 1) if horizontal else (1, length))
    n_segments = len(colors) - 1
    for i in range(length):
        t = i / max(1, length - 1)
        seg = min(int(t * n_segments), n_segments - 1)
        local_t = (t * n_segments) - seg
        c0 = Image.new("RGB", (1, 1), colors[seg]).getpixel((0, 0))
        c1 = Image.new("RGB", (1, 1), colors[seg + 1]).getpixel((0, 0))
        rgb = tuple(round(c0[k] + (c1[k] - c0[k]) * local_t) for k in range(3))
        if horizontal:
            strip.putpixel((i, 0), rgb)
        else:
            strip.putpixel((0, i), rgb)
    return strip.resize(size, Image.BILINEAR)


def _fit_font(font_path: str, phrase: str, stroke_width: int):
    """캔버스 최대 너비 안에 들어갈 때까지 폰트 크기를 줄여가며 맞는 크기를 찾는다."""
    measurer = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font_size = 240
    while font_size > 20:
        font = ImageFont.truetype(font_path, font_size)
        bbox = measurer.multiline_textbbox((0, 0), phrase, font=font, stroke_width=stroke_width, align="center")
        if bbox[2] - bbox[0] <= MAX_TEXT_WIDTH:
            return font, bbox
        font_size -= 4
    return font, bbox


def _prepare_text_layers(phrase: str, font_key: str, style: StylePreset):
    """정지/애니메이션 렌더링에서 공통으로 쓰는 (캔버스 크기, 외곽선 레이어, 채우기 마스크,
    채우기 색 레이어)를 만든다. 배경만 정지/애니메이션에서 다르게 합성한다."""
    font_path = _font_path(font_key)
    font, bbox = _fit_font(font_path, phrase, style.stroke_width)

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    canvas_w = max(1, round(text_w + PADDING_X * 2))
    canvas_h = max(1, round(text_h + PADDING_Y * 2))
    text_x = PADDING_X - bbox[0]
    text_y = PADDING_Y - bbox[1]

    outline = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(outline).multiline_text(
        (text_x, text_y),
        phrase,
        font=font,
        fill=style.stroke_color,
        stroke_width=style.stroke_width,
        stroke_fill=style.stroke_color,
        align="center",
    )

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    ImageDraw.Draw(mask).multiline_text((text_x, text_y), phrase, font=font, fill=255, align="center")

    fill_layer = _make_gradient((canvas_w, canvas_h), style.text_colors, horizontal=False).convert("RGBA")

    return canvas_w, canvas_h, outline, mask, fill_layer


def _compose(bg: Image.Image, outline: Image.Image, mask: Image.Image, fill_layer: Image.Image) -> Image.Image:
    canvas = bg.convert("RGBA")
    canvas = Image.alpha_composite(canvas, outline)
    canvas.paste(fill_layer, (0, 0), mask)
    return canvas


def render_text_emoji(phrase: str, font_key: str, style_key: str) -> bytes:
    """문구 + 폰트 + 색상 프리셋으로 정지 이미지를 만들어 PNG 바이트로 반환한다."""
    phrase = phrase.strip()[:MAX_PHRASE_LEN]
    if not phrase:
        raise ValueError("문구가 비어 있습니다")
    style = STYLES.get(style_key)
    if style is None:
        raise ValueError(f"알 수 없는 스타일: {style_key}")

    canvas_w, canvas_h, outline, mask, fill_layer = _prepare_text_layers(phrase, font_key, style)
    bg = _make_gradient((canvas_w, canvas_h), style.bg_colors, horizontal=False)
    canvas = _compose(bg, outline, mask, fill_layer)

    buf = BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def render_text_emoji_animated(phrase: str, font_key: str, style_key: str, out_path: str) -> None:
    """문구 + 폰트 + 색상 프리셋으로, 배경 그라데이션이 좌우로 흐르는 짧은(3초) 애니메이션
    배너를 만들어 out_path에 WEBM(VP9, 알파 없음)으로 저장한다."""
    phrase = phrase.strip()[:MAX_PHRASE_LEN]
    if not phrase:
        raise ValueError("문구가 비어 있습니다")
    style = STYLES.get(style_key)
    if style is None:
        raise ValueError(f"알 수 없는 스타일: {style_key}")

    canvas_w, canvas_h, outline, mask, fill_layer = _prepare_text_layers(phrase, font_key, style)

    # 색을 이어붙여(마지막에 시작 색을 다시 붙임) 이음매 없이 반복되는 폭 2*canvas_w짜리
    # 그라데이션 띠를 만든 뒤, 프레임마다 다른 위치에서 canvas_w 폭만큼 잘라내면
    # 좌우로 흐르는 애니메이션이 된다.
    loop_colors = tuple(style.wave_colors) + (style.wave_colors[0],)
    strip = _make_gradient((canvas_w * 2, canvas_h), loop_colors, horizontal=True)

    frames_dir = tempfile.mkdtemp(prefix="text_emoji_frames_")
    try:
        for i in range(ANIM_FRAMES):
            offset = int(canvas_w * i / ANIM_FRAMES)
            bg_frame = strip.crop((offset, 0, offset + canvas_w, canvas_h))
            frame = _compose(bg_frame, outline, mask, fill_layer).convert("RGB")
            frame.save(os.path.join(frames_dir, f"f{i:03d}.png"))

        cmd = [
            ffmpeg_path(),
            "-y",
            "-framerate",
            str(ANIM_FPS),
            "-i",
            os.path.join(frames_dir, "f%03d.png"),
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "28",
            "-b:v",
            "0",
            out_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"애니메이션 인코딩 실패: {exc.stderr.decode(errors='ignore')[-500:]}"
            ) from exc
    finally:
        for name in os.listdir(frames_dir):
            os.remove(os.path.join(frames_dir, name))
        os.rmdir(frames_dir)
