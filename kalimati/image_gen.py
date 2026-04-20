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
from typing import Iterable, Literal

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

# Min wholesale price change vs prior snapshot (same basis as notifications).
_PRICE_MOVE_EPS = 0.01

PriceMove = Literal["down", "up", "neutral"]


def _min_price_movement(current: PriceRow, previous: PriceRow | None) -> PriceMove:
    if previous is None:
        return "neutral"
    cm = current.min_price
    pm = previous.min_price
    if cm is None or pm is None:
        return "neutral"
    if cm < pm - _PRICE_MOVE_EPS:
        return "down"
    if cm > pm + _PRICE_MOVE_EPS:
        return "up"
    return "neutral"


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
                "pithumbsize": 1280,
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
    path = cache_dir / f"{_slug(commodity)}_hq.jpg"
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
            im.save(path, format="JPEG", quality=93, optimize=True)
            return im
        except (requests.RequestException, OSError):
            pass

    ph = Image.new("RGB", (512, 512), PHOTO_HALO)
    d = ImageDraw.Draw(ph)
    d.text((196, 216), "?", fill=PHOTO_RING, font=_font_sans(144, bold=True))
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


def _draw_frame(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    *,
    line_scale: float = 1.0,
) -> None:
    x0, y0, x1, y1 = rect
    ow = max(3, int(round(4 * line_scale)))
    iw = max(2, int(round(2 * line_scale)))
    inset = max(8, int(round(10 * line_scale)))
    corner = max(4, int(round(6 * line_scale)))
    s = max(16, int(round(22 * line_scale)))
    w_arc = max(2, int(round(3 * line_scale)))
    draw.rectangle(rect, outline=FRAME_OUTER, width=ow)
    draw.rectangle(
        (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
        outline=_FRAME_INNER,
        width=iw,
    )
    for cx, cy, a0, a1 in (
        (x0 + corner, y0 + corner, 0, 90),
        (x1 - corner, y0 + corner, 90, 180),
        (x1 - corner, y1 - corner, 180, 270),
        (x0 + corner, y1 - corner, 270, 360),
    ):
        draw.arc((cx - s, cy - s, cx + s, cy + s), a0, a1, fill=PHOTO_RING, width=w_arc)


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
    rw = max(2, min(8, diameter // 30))
    ring.ellipse((2, 2, diameter - 3, diameter - 3), outline=PHOTO_RING, width=rw)
    return out


@dataclass(frozen=True)
class CellSentimentStyle:
    fill: tuple[int, int, int]
    border: tuple[int, int, int]
    price: tuple[int, int, int]
    name: tuple[int, int, int]
    badge: tuple[int, int, int]
    badge_text: str


def _cell_style(move: PriceMove) -> CellSentimentStyle:
    if move == "down":
        return CellSentimentStyle(
            fill=(228, 244, 232),
            border=(34, 132, 76),
            price=(18, 92, 52),
            name=(42, 58, 46),
            badge=(26, 118, 68),
            badge_text="घट्यो",
        )
    if move == "up":
        return CellSentimentStyle(
            fill=(255, 232, 228),
            border=(188, 72, 62),
            price=(130, 42, 36),
            name=(58, 44, 42),
            badge=(168, 52, 44),
            badge_text="बढ्यो",
        )
    return CellSentimentStyle(
        fill=(238, 236, 230),
        border=(130, 122, 110),
        price=TEXT_TITLE,
        name=TEXT_BODY,
        badge=(105, 96, 84),
        badge_text="जस्तै",
    )


@dataclass
class PosterConfig:
    """High-resolution poster (2× baseline) for sharper PNGs and social sharing."""

    width: int = 2160
    cols: int = 6
    rows: int = 2
    frame_margin: int = 52
    content_pad: int = 80
    header_h: int = 320
    footer_h: int = 100
    cell_photo: int = 236
    gap_after_photo: int = 20
    badge_h: int = 44


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
    Each cell is tinted by min-price movement vs the prior day: green (घट्यो), red (बढ्यो), neutral (जस्तै).
    """
    prev_map = previous_by_commodity or {}
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

    sc = max(1, cfg.width // 1080)
    font_title = _font_noto(bold_path, 44 * sc)
    font_date = _font_noto(reg_path, 19 * sc)
    font_name = _font_noto(bold_path, 22 * sc)
    font_price = _font_noto(reg_path, 21 * sc)
    font_badge = _font_noto(reg_path, 15 * sc)
    font_foot = _font_noto(reg_path, 17 * sc)

    inner_w = cfg.width - 2 * (cfg.frame_margin + cfg.content_pad)
    cell_w = inner_w // cfg.cols
    line_scale = cfg.width / 1080.0
    # row height: photo + name (2 lines) + price + badge
    row_h = cfg.cell_photo + cfg.gap_after_photo + 120 * sc + 52 * sc + cfg.badge_h + 24
    height = (
        2 * cfg.frame_margin
        + cfg.header_h
        + cfg.rows * row_h
        + cfg.footer_h
        + 32
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
        _draw_frame(draw, frame, line_scale=line_scale)

        cx0 = cfg.frame_margin + cfg.content_pad
        y_top = cfg.frame_margin + 28 * sc
        date_txt = day.strftime("%Y-%m-%d")
        dw, dh = _text_size(draw, date_txt, font_date)
        draw.text(((cfg.width - dw) // 2, y_top), date_txt, fill=TEXT_TITLE, font=font_date)
        title = "तरकारीको मूल्य सूची"
        tw, th = _text_size(draw, title, font_title)
        draw.text(((cfg.width - tw) // 2, y_top + dh + 28 * sc), title, fill=TEXT_TITLE, font=font_title)

        grid_top = cfg.frame_margin + cfg.header_h
        gx0 = cx0
        inner_bottom = height - cfg.frame_margin - cfg.footer_h - 16

        # light grid guides
        gw = max(1, int(round(1 * line_scale)))
        for c in range(1, cfg.cols):
            x = gx0 + c * cell_w
            draw.line((x, grid_top, x, inner_bottom), fill=(220, 208, 190), width=gw)
        mid_y = grid_top + row_h
        draw.line((gx0, mid_y, gx0 + inner_w, mid_y), fill=(220, 208, 190), width=gw)

        slots = chunk + [None] * (per_page - len(chunk))
        for idx, row in enumerate(slots):
            if row is None:
                continue
            r, c = divmod(idx, cfg.cols)
            cell_x0 = gx0 + c * cell_w
            cx = cell_x0 + cell_w // 2
            cy_photo = grid_top + r * row_h + cfg.cell_photo // 2 + 6

            move = _min_price_movement(row, prev_map.get(row.commodity))
            st = _cell_style(move)
            cell_pad = int(round(5 * line_scale))
            rad = int(round(14 * line_scale))
            cell_top = grid_top + r * row_h
            rx0 = cell_x0 + cell_pad
            ry0 = cell_top + cell_pad
            rx1 = cell_x0 + cell_w - cell_pad
            ry1 = cell_top + row_h - cell_pad
            draw.rounded_rectangle(
                (rx0, ry0, rx1, ry1),
                radius=rad,
                fill=st.fill,
                outline=st.border,
                width=2,
            )

            term = _search_term_for_commodity(row.commodity)
            raw = _load_or_fetch_thumb(session, cache_dir, row.commodity, term)
            circ = _circle_photo_rgba(raw, cfg.cell_photo)
            paste_x = cx - cfg.cell_photo // 2
            paste_y = cy_photo - cfg.cell_photo // 2
            img.paste(circ, (paste_x, paste_y), circ)

            nm = _display_name(row.commodity)
            wrap_w = max(14, 13 * sc)
            lines = textwrap.wrap(nm, width=wrap_w)[:2]
            ty = paste_y + cfg.cell_photo + cfg.gap_after_photo
            for line in lines:
                lw, lh = _text_size(draw, line, font_name)
                draw.text((cx - lw // 2, ty), line, fill=st.name, font=font_name)
                ty += lh + 2

            unit = _extract_unit(row.commodity)
            pl = _price_line(row.min_price, unit)
            pw, ph = _text_size(draw, pl, font_price)
            pl_y = ty + 2
            draw.text((cx - pw // 2, pl_y), pl, fill=st.price, font=font_price)
            bw, _ = _text_size(draw, st.badge_text, font_badge)
            draw.text(
                (cx - bw // 2, pl_y + ph + 4),
                st.badge_text,
                fill=st.badge,
                font=font_badge,
            )

        foot = "मूल्य दैनिक परिवर्तन हुन सक्छ"
        fw, fh = _text_size(draw, foot, font_foot)
        draw.text(((cfg.width - fw) // 2, height - cfg.frame_margin - cfg.footer_h // 2 - fh // 2), foot, fill=TEXT_MUTED, font=font_foot)

        out_path = day_dir / f"page-{page:02d}.png"
        img.save(out_path, format="PNG", optimize=True, compress_level=6)
        paths.append(out_path)
        page += 1

    session.close()
    return paths
