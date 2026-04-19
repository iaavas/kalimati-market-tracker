from __future__ import annotations

from pathlib import Path

import requests

from kalimati.config import FACEBOOK_ACCESS_TOKEN, FACEBOOK_PAGE_ID


def upload_page_photo(image_path: Path, caption: str) -> None:
    if not FACEBOOK_PAGE_ID or not FACEBOOK_ACCESS_TOKEN:
        return
    url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/photos"
    with image_path.open("rb") as f:
        files = {"source": f}
        data = {"caption": caption, "access_token": FACEBOOK_ACCESS_TOKEN}
        r = requests.post(url, data=data, files=files, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Facebook upload failed: {r.status_code} {r.text}")
