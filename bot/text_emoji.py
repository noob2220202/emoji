"""문구를 받아 폰트/색상 스타일을 입힌 정지 이미지를 만드는 "글자 이모지화" 기능.

여기서 만들어진 이미지는 이후 image_split.py의 동일한 분할 파이프라인에 그대로
들어가서, 사진을 보냈을 때와 똑같이 이모지 팩으로 쪼개진다.
"""

import os
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

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
    bg_colors: Tuple[str, ...]  # 1개면 단색 배경, 2개 이상이면 세로 그라데이션
    text_colors: Tuple[str, ...]  # 1개면 단색 글자, 2개 이상이면 가로 그라데이션
    stroke_color: str
    stroke_width: int
    glow_color: Optional[str] = None  # 지정하면 글자 뒤에 같은 색의 블러 후광을 넣는다


STYLES: Dict[str, StylePreset] = {
    "gold_premium": StylePreset(
        label="👑 골드 프리미엄",
        bg_colors=("#2a2110", "#000000"),
        text_colors=("#fff6c8", "#d4af37"),
        stroke_color="#4a3a0e",
        stroke_width=4,
    ),
    "neon_pink": StylePreset(
        label="💖 네온 핑크",
        bg_colors=("#1a0022",),
        text_colors=("#ff2fb1",),
        stroke_color="#ffffff",
        stroke_width=2,
        glow_color="#ff2fb1",
    ),
    "neon_green": StylePreset(
        label="🟢 형광 그린",
        bg_colors=("#001a08",),
        text_colors=("#39ff6a",),
        stroke_color="#ffffff",
        stroke_width=2,
        glow_color="#39ff6a",
    ),
    "pastel_dream": StylePreset(
        label="🌸 파스텔 드림",
        bg_colors=("#ffe1f0", "#e0f0ff"),
        text_colors=("#7a5c8e",),
        stroke_color="#ffffff",
        stroke_width=5,
    ),
    "rainbow": StylePreset(
        label="🌈 레인보우",
        bg_colors=("#101010",),
        text_colors=("#ff3b3b", "#ffd93b", "#3bff6e", "#3bd4ff", "#a03bff"),
        stroke_color="#ffffff",
        stroke_width=2,
    ),
}

MAX_TEXT_WIDTH = 1400
PADDING_X = 90
PADDING_Y = 90
MAX_PHRASE_LEN = 30


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


def _fit_font(font_path: str, phrase: str, stroke_width: int) -> Tuple[ImageFont.FreeTypeFont, Tuple[float, float, float, float]]:
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


def render_text_emoji(phrase: str, font_key: str, style_key: str) -> bytes:
    """문구 + 폰트 + 스타일 프리셋으로 스타일링된 PNG 이미지를 만들어 바이트로 반환한다."""
    phrase = phrase.strip()[:MAX_PHRASE_LEN]
    if not phrase:
        raise ValueError("문구가 비어 있습니다")

    style = STYLES.get(style_key)
    if style is None:
        raise ValueError(f"알 수 없는 스타일: {style_key}")

    font_path = _font_path(font_key)
    font, bbox = _fit_font(font_path, phrase, style.stroke_width)

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    canvas_w = max(1, round(text_w + PADDING_X * 2))
    canvas_h = max(1, round(text_h + PADDING_Y * 2))
    text_x = PADDING_X - bbox[0]
    text_y = PADDING_Y - bbox[1]

    bg = _make_gradient((canvas_w, canvas_h), style.bg_colors, horizontal=False)
    canvas = bg.convert("RGBA")

    if style.glow_color:
        glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        ImageDraw.Draw(glow).multiline_text(
            (text_x, text_y), phrase, font=font, fill=style.glow_color, align="center"
        )
        glow = glow.filter(ImageFilter.GaussianBlur(max(4, canvas_h // 18)))
        canvas = Image.alpha_composite(canvas, glow)

    # 외곽선(테두리)을 단색으로 두껍게 깔아둔 뒤, 그 위에 글자 속(단색/그라데이션)을 덮어 그린다.
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
    canvas = Image.alpha_composite(canvas, outline)

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    ImageDraw.Draw(mask).multiline_text((text_x, text_y), phrase, font=font, fill=255, align="center")
    fill_layer = _make_gradient((canvas_w, canvas_h), style.text_colors, horizontal=True).convert("RGBA")
    canvas.paste(fill_layer, (0, 0), mask)

    buf = BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
