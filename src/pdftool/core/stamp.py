"""ページ番号・透かし・ラベル（スタンプ）の追加。

GUI 非依存。PySide6 を import しないこと。

テキストは Pillow で透明 PNG に描いて（日本語もそのまま描ける）、アルファ付きの画像として
ページに重ねる。PDF に日本語フォントを埋め込む必要が無く、見た目は Windows の
フォントのまま。文字は画像になるので、スタンプ部分の文字の選択・検索はできない
（元のページの本文には影響しない）。解像度は 1pt あたり 4px（約 288dpi）。

同じ内容のスタンプ（透かしなど）は 1 つの画像オブジェクトを全ページで共有するので、
ページ数が増えてもファイルサイズはほとんど増えない。ページ番号のようにページごとに
内容が変わるものはページごとの小さな画像になる。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PageObject, PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from pdftool.core.document import PdfError

PX_PER_PT = 4.0

POSITIONS = (
    "top-left", "top-center", "top-right",
    "center",
    "bottom-left", "bottom-center", "bottom-right",
)
POSITION_LABELS = {
    "top-left": "左上", "top-center": "上中央", "top-right": "右上",
    "center": "中央",
    "bottom-left": "左下", "bottom-center": "下中央", "bottom-right": "右下",
}

_FONT_CANDIDATES = (
    "YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "BIZ-UDGothicR.ttc", "YuGothR.ttc",
    "arial.ttf", "segoeui.ttf",
)


class StampError(PdfError):
    """スタンプの生成に失敗した（フォントが無い等）。"""


@dataclass(frozen=True)
class TextStamp:
    """1 つのテキストスタンプ。`text` 中の {n}=ページ番号、{total}=総ページ数 を置換する。"""

    text: str
    position: str = "bottom-center"
    size_pt: float = 10.0
    color: tuple[int, int, int] = (0, 0, 0)
    opacity: float = 1.0          # 0..1
    angle: float = 0.0            # 反時計回りの度数（透かしは 45 など）
    margin_pt: float = 24.0       # 用紙の端からの距離（center では無視）
    border: bool = False          # 文字を枠線で囲む（「配布用」などのラベル向け）
    bold: bool = False            # 太字っぽく見せる（縁取りで太らせる）
    start: int = 1                # {n} の開始番号
    skip_first: int = 0           # 先頭から何ページはスタンプしないか（表紙など）

    def __post_init__(self):
        if self.position not in POSITIONS:
            raise ValueError(f"不明な位置です: {self.position}")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("opacity は 0〜1 で指定してください")
        if self.size_pt <= 0:
            raise ValueError("文字サイズは正の値を指定してください")
        if not self.text.strip():
            raise ValueError("スタンプの文字が空です")


# ---------------------------------------------------------------------------
# プリセット
# ---------------------------------------------------------------------------
def page_number_stamp(fmt: str = "{n}", position: str = "bottom-center", size_pt: float = 10.0,
                      start: int = 1, skip_first: int = 0) -> TextStamp:
    return TextStamp(text=fmt, position=position, size_pt=size_pt, start=start, skip_first=skip_first)


def watermark_stamp(text: str, size_pt: float = 80.0, opacity: float = 0.15, angle: float = 45.0,
                    color: tuple[int, int, int] = (128, 128, 128)) -> TextStamp:
    return TextStamp(text=text, position="center", size_pt=size_pt, color=color, opacity=opacity,
                     angle=angle, bold=True)


def label_stamp(text: str, position: str = "top-right", size_pt: float = 14.0,
                color: tuple[int, int, int] = (200, 0, 0)) -> TextStamp:
    return TextStamp(text=text, position=position, size_pt=size_pt, color=color, border=True, bold=True)


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
def _find_font_file() -> Path:
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in _FONT_CANDIDATES:
        p = fonts_dir / name
        if p.is_file():
            return p
    raise StampError(
        "文字の描画に使えるフォントが見つかりませんでした（C:\\Windows\\Fonts に日本語フォントが必要です）"
    )


def render_stamp_image(stamp: TextStamp, text: str) -> Image.Image:
    """スタンプを透明背景の RGBA 画像に描く（画像の 1px = 1/PX_PER_PT pt）。"""
    font = ImageFont.truetype(str(_find_font_file()), max(1, round(stamp.size_pt * PX_PER_PT)))
    lines = text.split("\n")
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    boxes = [probe.textbbox((0, 0), ln or " ", font=font) for ln in lines]
    line_h = max(b[3] for b in boxes) + round(stamp.size_pt * PX_PER_PT * 0.25)
    text_w = max(b[2] for b in boxes)
    pad = round(stamp.size_pt * PX_PER_PT * (0.45 if stamp.border else 0.15))
    stroke = max(1, round(stamp.size_pt * PX_PER_PT * 0.03)) if stamp.bold else 0
    w = text_w + (pad + stroke) * 2
    h = line_h * len(lines) + (pad + stroke) * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = tuple(int(c) for c in stamp.color)
    for k, ln in enumerate(lines):
        d.text((pad + stroke, pad + stroke + k * line_h), ln, font=font, fill=rgb + (255,),
               stroke_width=stroke, stroke_fill=rgb + (255,))
    if stamp.border:
        bw = max(2, round(stamp.size_pt * PX_PER_PT * 0.08))
        d.rectangle((bw // 2, bw // 2, w - 1 - bw // 2, h - 1 - bw // 2), outline=rgb + (255,), width=bw)
    if stamp.angle % 360:
        img = img.rotate(stamp.angle, expand=True, resample=Image.Resampling.BICUBIC)
    if stamp.opacity < 1.0:
        a = img.getchannel("A").point(lambda v: round(v * stamp.opacity))
        img.putalpha(a)
    return img


def _image_xobject(writer: PdfWriter, img: Image.Image):
    """RGBA 画像を、アルファ(SMask)付きの画像 XObject として writer に登録して参照を返す。"""
    def make(data: bytes, colorspace: str, smask=None):
        obj = DecodedStreamObject()
        obj.set_data(data)
        obj.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(img.width),
            NameObject("/Height"): NumberObject(img.height),
            NameObject("/ColorSpace"): NameObject(colorspace),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        if smask is not None:
            obj[NameObject("/SMask")] = smask
        # flate_encode() は圧縮済みの「新しい」オブジェクトを返す（元は変わらない）
        return writer._add_object(obj.flate_encode())

    alpha = make(img.getchannel("A").tobytes(), "/DeviceGray")
    return make(img.convert("RGB").tobytes(), "/DeviceRGB", smask=alpha)


class StampPainter:
    """1 つの出力 PDF（PdfWriter）に対してスタンプを重ねる。同じ画像は共有する。"""

    def __init__(self, writer: PdfWriter):
        self.writer = writer
        self._cache: dict[tuple, tuple] = {}

    def _image(self, stamp: TextStamp, text: str):
        key = (stamp, text)
        hit = self._cache.get(key)
        if hit is None:
            img = render_stamp_image(stamp, text)
            hit = self._cache[key] = (_image_xobject(self.writer, img), img.width, img.height)
        return hit

    def stamp_page(self, page: PageObject, stamps: list[TextStamp], index: int, total: int) -> None:
        """page（回転 0 に焼き込み済み・mediabox が見た目）に、番号 index(0 始まり) のスタンプを重ねる。"""
        placed = []
        for st in stamps:
            if index < st.skip_first:
                continue
            text = st.text.replace("{n}", str(st.start + index - st.skip_first)).replace("{total}", str(total))
            placed.append((st, text))
        if not placed:
            return
        mb = page.mediabox
        left, bottom = float(mb.left), float(mb.bottom)
        pw, ph = float(mb.width), float(mb.height)

        xobjects = DictionaryObject()
        ops = []
        for k, (st, text) in enumerate(placed):
            ref, wpx, hpx = self._image(st, text)
            w, h = wpx / PX_PER_PT, hpx / PX_PER_PT
            m = st.margin_pt
            x = {"left": m, "center": (pw - w) / 2, "right": pw - w - m}[
                "center" if st.position in ("center", "top-center", "bottom-center")
                else ("left" if st.position.endswith("left") else "right")
            ]
            y = {"top": ph - h - m, "bottom": m, "center": (ph - h) / 2}[
                "center" if st.position == "center" else st.position.split("-")[0]
            ]
            name = f"/PdfToolStamp{k}"
            xobjects[NameObject(name)] = ref
            ops.append(f"q {w:.3f} 0 0 {h:.3f} {left + x:.3f} {bottom + y:.3f} cm {name} Do Q")

        overlay = PageObject.create_blank_page(None, pw, ph)
        overlay.mediabox = mb
        overlay[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
        cs = DecodedStreamObject()
        cs.set_data("\n".join(ops).encode("ascii"))
        overlay[NameObject("/Contents")] = self.writer._add_object(cs)
        page.merge_page(overlay)

