from __future__ import annotations

import hashlib
import random
import re
import textwrap
import time
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

from kalimati.config import BASE_DIR, OUTPUT_DIR
from kalimati.db import PriceRow

_WIKI_HEADERS = {
    "User-Agent": "KalimatiPriceTracker/1.0 (educational; https://kalimatimarket.gov.np) python-requests",
}

_NOTO_REG_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-fonts/main/"
    "hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf"
)
_NOTO_BOLD_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-fonts/main/"
    "hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
)

_LAT_TO_DEV = str.maketrans("0123456789", "०१२३४५६७८९")

# Poster palette (warm parchment + bronze frame)
PARCHMENT = (248, 242, 232)
PARCHMENT_DEEP = (238, 228, 212)
FRAME_OUTER = (120, 92, 62)
_FRAME_INNER = (200, 176, 140)
TEXT_TITLE = (52, 40, 28)
TEXT_BODY = (58, 46, 34)
TEXT_MUTED = (120, 106, 90)
PHOTO_RING = (176, 140, 89)
PHOTO_HALO = (255, 248, 235)


def _to_devanagari_numerals(s: str) -> str:
    return s.translate(_LAT_TO_DEV)


def _numeral_dev_number(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-6:
        return _to_devanagari_numerals(str(int(round(v))))
    a, b = f"{v:.2f}".split(".")
    return _to_devanagari_numerals(a) + "." + _to_devanagari_numerals(b)


def _font_dir() -> Path:
    d = BASE_DIR / "data" / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_noto(session: requests.Session) -> tuple[Path, Path]:
    """Prefer Noto Sans Devanagari for clarity; download once into data/fonts."""
    d = _font_dir()
    reg = d / "NotoSansDevanagari-Regular.ttf"
    bold = d / "NotoSansDevanagari-Bold.ttf"
    if not reg.exists():
        r = session.get(_NOTO_REG_URL, timeout=120)
        r.raise_for_status()
        reg.write_bytes(r.content)
    if not bold.exists():
        r = session.get(_NOTO_BOLD_URL, timeout=120)
        r.raise_for_status()
        bold.write_bytes(r.content)
    return reg, bold


def _font_noto(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _font_sans(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", size=min(size, 40))
    except OSError:
        return ImageFont.load_default()


def _slug(s: str) -> str:
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^\w\-]+", "_", s)[:40]
    return f"{safe}_{h}"


def _display_name(commodity: str) -> str:
    s = re.sub(r"\s*\([^)]*\)", "", commodity)
    return re.sub(r"\s+", " ", s).strip()


def _extract_unit(commodity: str) -> str:
    """Pick the parenthetical that looks like a unit (Kalimati often has variety first, e.g. (भारतीय) (केजी))."""
    parts = re.findall(r"\(([^)]*)\)", commodity)
    if not parts:
        return "केजी"

    def classify(inner: str) -> str | None:
        s = inner.strip()
        if "मुठा" in s:
            return "मुठा"
        if "दर्जन" in s:
            return "दर्जन"
        if "गोटा" in s:
            return "गोटा"
        if "प्रति" in s:
            return s.replace("  ", " ")
        compact = s.replace(" ", "").replace(".", "")
        if "केजी" in s or "केजी" in compact:
            return "केजी"
        return None

    for inner in reversed(parts):
        u = classify(inner)
        if u:
            return u
    return "केजी"


def _price_line(min_v: float | None, unit: str) -> str:
    return f"रु.{_numeral_dev_number(min_v)}/{unit}"


def _search_term_for_commodity(name: str) -> str:
    rules: list[tuple[str, str]] = [
        ("गोलभेडा", "tomato"),
        ("आलु", "potato"),
        ("प्याज", "onion"),
        ("गाजर", "carrot"),
        ("काउली", "cauliflower"),
        ("मूला", "daikon radish"),
        ("भन्टा", "eggplant"),
        ("करेला", "bitter melon"),
        ("लौका", "bottle gourd"),
        ("भिण्डी", "okra"),
        ("बन्दा", "yardlong bean"),
        ("बोडी", "yardlong bean"),
        ("मटरकोशा", "snow pea pods"),
        ("घिउ सिमी", "broad bean"),
        ("टाटे सिमी", "green bean"),
        ("भटमास", "edamame"),
        ("चिचिण्डो", "ivy gourd"),
        ("घिरौला", "snake gourd"),
        ("फर्सी", "pumpkin"),
        ("सखरखण्ड", "sweet potato"),
        ("बरेला", "sponge gourd"),
        ("पिंडालू", "taro corm"),
        ("स्कूस", "chayote"),
        ("रायो", "mustard greens"),
        ("पालूगो", "spinach"),
        ("चमसूर", "garden cress"),
        ("तोरीको साग", "mustard greens"),
        ("मेथी", "fenugreek leaves"),
        ("प्याज हरियो", "green onion"),
        ("बकूला", "jute mallow"),
        ("च्याउ", "mushroom"),
        ("ब्रोकाउली", "broccoli"),
        ("चुकुन्दर", "beetroot"),
        ("सजिवन", "drumstick vegetable"),
        ("कोइरालो", "edible fern"),
        ("रातो बन्दा", "amaranth leaves"),
        ("जिरीको साग", "cress"),
        ("कोबी", "cabbage"),
        ("सेलरी", "celery"),
        ("सौफ", "dill"),
        ("पुदीना", "mint"),
        ("गान्टे मूला", "turnip"),
        ("इमली", "tamarind"),
        ("तामा", "bamboo shoot food"),
        ("तोफु", "tofu"),
        ("गुन्दुक", "pickled vegetable"),
        ("स्याउ", "apple fruit"),
        ("केरा", "banana"),
        ("कागती", "lemon"),
        ("अनार", "pomegranate"),
        ("अंगुर", "grapes"),
        ("सुन्तला", "orange fruit"),
        ("तरबुज", "watermelon"),
        ("भुई कटहर", "jackfruit"),
        ("काक्रो", "cucumber"),
        ("रुख कटहर", "breadfruit"),
        ("मेवा", "papaya"),
        ("किवि", "kiwifruit"),
        ("आभोकाडो", "avocado"),
        ("अदुवा", "ginger root"),
        ("खु्र्सानी", "chili pepper"),
        ("खुर्सानी", "chili pepper"),
        ("लसुन", "garlic"),
        ("धनिया", "coriander"),
        ("छ्यापी", "yam"),
        ("माछा", "fish food"),
        ("न्यूरो", "yam"),
        ("ग्याठ", "kohlrabi"),
        ("परवर", "pointed gourd"),
        ("तितो करेला", "bitter melon"),
        ("सेतो मूला", "turnip"),
    ]
    for key, term in rules:
        if key in name:
            return term
    return "vegetable"


def _wiki_thumbnail_url(session: requests.Session, search_term: str) -> str | None:
    try:
        r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": search_term,
                "srlimit": 1,
                "format": "json",
            },
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        page_title = hits[0].get("title")
        if not page_title:
            return None
        r2 = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "titles": page_title,
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": 640,
            },
            timeout=20,
        )
        r2.raise_for_status()
        pages = r2.json().get("query", {}).get("pages", {})
        for _pid, pdata in pages.items():
            thumb = pdata.get("thumbnail", {}).get("source")
            if thumb:
                return thumb
    except (requests.RequestException, KeyError, ValueError):
        return None
    return None


def _load_or_fetch_thumb(
    session: requests.Session,
    cache_dir: Path,
    commodity: str,
    search_term: str,
) -> Image.Image:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_slug(commodity)}.jpg"
    if path.exists():
        try:
            return Image.open(path).convert("RGB")
        except OSError:
            path.unlink(missing_ok=True)

    time.sleep(0.12)
    url = _wiki_thumbnail_url(session, search_term)
    if url:
        try:
            resp = session.get(url, timeout=25)
            resp.raise_for_status()
            im = Image.open(BytesIO(resp.content)).convert("RGB")
            im.save(path, format="JPEG", quality=88, optimize=True)
            return im
        except (requests.RequestException, OSError):
            pass

    ph = Image.new("RGB", (256, 256), PHOTO_HALO)
    d = ImageDraw.Draw(ph)
    d.text((100, 108), "?", fill=PHOTO_RING, font=_font_sans(72, bold=True))
    return ph


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _parchment_canvas(width: int, height: int) -> Image.Image:
    base = Image.new("RGB", (width, height), PARCHMENT)
    tint = Image.new("RGB", (width, height), PARCHMENT_DEEP)
    arr = Image.blend(base, tint, 0.14).copy()
    px = arr.load()
    rnd = random.Random(42)
    for _ in range(max(800, width * height // 500)):
        x, y = rnd.randint(2, width - 3), rnd.randint(2, height - 3)
        r, g, b = px[x, y]
        n = rnd.randint(-5, 5)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    arr = arr.filter(ImageFilter.GaussianBlur(radius=0.4))
    return arr


def _draw_frame(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = rect
    draw.rectangle(rect, outline=FRAME_OUTER, width=4)
    draw.rectangle((x0 + 10, y0 + 10, x1 - 10, y1 - 10), outline=_FRAME_INNER, width=2)
    s = 22
    for cx, cy, a0, a1 in (
        (x0 + 6, y0 + 6, 0, 90),
        (x1 - 6, y0 + 6, 90, 180),
        (x1 - 6, y1 - 6, 180, 270),
        (x0 + 6, y1 - 6, 270, 360),
    ):
        draw.arc((cx - s, cy - s, cx + s, cy + s), a0, a1, fill=PHOTO_RING, width=3)


def _circle_photo_rgba(im: Image.Image, diameter: int) -> Image.Image:
    im = ImageOps.fit(im, (diameter, diameter), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    halo = Image.new("RGB", (diameter, diameter), PHOTO_HALO)
    halo.paste(im, (0, 0), mask)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(halo, (0, 0))
    out.paste(im, (0, 0), mask)
    ring = ImageDraw.Draw(out)
    ring.ellipse((2, 2, diameter - 3, diameter - 3), outline=PHOTO_RING, width=4)
    return out


@dataclass
class PosterConfig:
    width: int = 1080
    cols: int = 6
    rows: int = 2
    frame_margin: int = 26
    content_pad: int = 40
    title_h: int = 108
    footer_h: int = 56
    cell_photo: int = 118
    gap_after_photo: int = 10


def render_price_grid_png(
    day: date,
    rows: Iterable[PriceRow],
    out_root: Path | None = None,
    previous_by_commodity: dict[str, PriceRow] | None = None,
    **_: object,
) -> list[Path]:
    """
    Parchment-style poster: ``<out_root>/<YYYY-MM-DD>/page-NN.png``.
    6×2 grid (12 items), circular photos, Noto Sans Devanagari, ``रु.४५/केजी`` price lines.
    """
    _ = previous_by_commodity
    cfg = PosterConfig()
    per_page = cfg.cols * cfg.rows
    root = out_root or OUTPUT_DIR
    day_dir = root / day.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = BASE_DIR / "data" / "image_cache"

    rows_list = list(rows)
    session = requests.Session()
    session.headers.update(_WIKI_HEADERS)

    try:
        reg_path, bold_path = _ensure_noto(session)
    except (requests.RequestException, OSError):
        reg_path = Path("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc")
        bold_path = reg_path

    font_title = _font_noto(bold_path, 44)
    font_date = _font_noto(reg_path, 17)
    font_name = _font_noto(bold_path, 22)
    font_price = _font_noto(reg_path, 21)
    font_foot = _font_noto(reg_path, 17)

    inner_w = cfg.width - 2 * (cfg.frame_margin + cfg.content_pad)
    cell_w = inner_w // cfg.cols
    name_max_w = max(96, cell_w - 14)
    # row height from photo + text
    row_h = cfg.cell_photo + cfg.gap_after_photo + 56 + 36 + 8
    height = (
        2 * cfg.frame_margin
        + cfg.title_h
        + cfg.rows * row_h
        + cfg.footer_h
        + 24
    )

    paths: list[Path] = []
    page = 1
    for chunk_start in range(0, len(rows_list), per_page):
        chunk = rows_list[chunk_start : chunk_start + per_page]
        img = _parchment_canvas(cfg.width, height)
        draw = ImageDraw.Draw(img)

        frame = (
            cfg.frame_margin,
            cfg.frame_margin,
            cfg.width - cfg.frame_margin,
            height - cfg.frame_margin,
        )
        _draw_frame(draw, frame)

        cx0 = cfg.frame_margin + cfg.content_pad
        cy0 = cfg.frame_margin + cfg.title_h // 2 + 8

        title = "तरकारीको मूल्य सूची"
        tw, th = _text_size(draw, title, font_title)
        draw.text(((cfg.width - tw) // 2, cy0), title, fill=TEXT_TITLE, font=font_title)
        date_txt = day.strftime("%Y-%m-%d")
        dw, dh = _text_size(draw, date_txt, font_date)
        draw.text(((cfg.width - dw) // 2, cy0 + th + 6), date_txt, fill=TEXT_MUTED, font=font_date)

        grid_top = cfg.frame_margin + cfg.title_h + 8
        gx0 = cx0
        inner_bottom = height - cfg.frame_margin - cfg.footer_h - 8

        # light grid guides
        for c in range(1, cfg.cols):
            x = gx0 + c * cell_w
            draw.line((x, grid_top, x, inner_bottom), fill=(220, 208, 190), width=1)
        mid_y = grid_top + row_h
        draw.line((gx0, mid_y, gx0 + inner_w, mid_y), fill=(220, 208, 190), width=1)

        slots = chunk + [None] * (per_page - len(chunk))
        for idx, row in enumerate(slots):
            if row is None:
                continue
            r, c = divmod(idx, cfg.cols)
            cell_x0 = gx0 + c * cell_w
            cx = cell_x0 + cell_w // 2
            cy_photo = grid_top + r * row_h + cfg.cell_photo // 2 + 6

            term = _search_term_for_commodity(row.commodity)
            raw = _load_or_fetch_thumb(session, cache_dir, row.commodity, term)
            circ = _circle_photo_rgba(raw, cfg.cell_photo)
            paste_x = cx - cfg.cell_photo // 2
            paste_y = cy_photo - cfg.cell_photo // 2
            img.paste(circ, (paste_x, paste_y), circ)

            nm = _display_name(row.commodity)
            lines = textwrap.wrap(nm, width=14)[:2]
            ty = paste_y + cfg.cell_photo + cfg.gap_after_photo
            for line in lines:
                lw, lh = _text_size(draw, line, font_name)
                draw.text((cx - lw // 2, ty), line, fill=TEXT_BODY, font=font_name)
                ty += lh + 2

            unit = _extract_unit(row.commodity)
            pl = _price_line(row.min_price, unit)
            pw, ph = _text_size(draw, pl, font_price)
            draw.text((cx - pw // 2, ty + 2), pl, fill=TEXT_TITLE, font=font_price)

        foot = "मूल्य दैनिक परिवर्तन हुन सक्छ"
        fw, fh = _text_size(draw, foot, font_foot)
        draw.text(((cfg.width - fw) // 2, height - cfg.frame_margin - cfg.footer_h // 2 - fh // 2), foot, fill=TEXT_MUTED, font=font_foot)

        out_path = day_dir / f"page-{page:02d}.png"
        img.save(out_path, format="PNG", optimize=True)
        paths.append(out_path)
        page += 1

    session.close()
    return paths
