"""Pure rendering of a `ScrapeResult` into `post.md` and `metadata.json`.

`render_markdown` / `render_metadata` are network-free and side-effect-free:
they take a `ScrapeResult` plus the list of media filenames and return a
string / dict. `write_result` is the glue that downloads media (via instagrapi)
and writes the files.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from dataclasses import asdict
from pathlib import Path

from instascraper.models import Comment, Provenance, ScrapeResult
from instascraper.scraper import NullProgress

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXTS = (".mp4", ".mov")
MEDIA_EXTS = IMAGE_EXTS + VIDEO_EXTS


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _caveat(prov: Provenance, n_comments: int) -> str:
    """The comment-ranking honesty line for the provenance header."""
    if prov.comment_sort == "instagram":
        return (
            f"Comment ranking: first {n_comments} returned by Instagram "
            '(latest-first) — not the app\'s "top comments".'
        )
    # State what was *actually* paged: the humanized depth clamp and early-stop
    # make the real count differ from the configured limit, and the top-N is
    # ranked over that real set.
    limit = "no limit" if prov.comment_scan_limit == 0 else f"limit {prov.comment_scan_limit}"
    return (
        f"Comment ranking: top {n_comments} by like_count among "
        f"{prov.comments_scanned} comments scanned ({limit}) — "
        'a constructed ranking, not Instagram\'s in-app "top comments".'
    )


def render_markdown(result: ScrapeResult, media_files: list[str]) -> str:
    """Render `post.md` for a scraped post/reel."""
    lines: list[str] = []

    kind = "Reel" if result.is_video else "Post"
    lines.append(f"# @{result.owner} — {kind}")
    lines.append("")

    # Provenance / methods header as a blockquote.
    date_str = result.taken_at.date().isoformat() if result.taken_at else "unknown date"
    lines.append(f"> Posted {date_str} · ❤️ {result.likes:,} likes")
    lines.append(f"> Source: {result.source_url}")
    if result.provenance is not None:
        prov = result.provenance
        lines.append(
            f"> Fetched {prov.fetched_at} · {prov.tool} / "
            f"{prov.backend} · as @{prov.account}"
        )
        lines.append(f"> {_caveat(prov, len(result.comments))}")
        lines.append(f"> Pacing: humanization {prov.humanization}")
    lines.append("")

    # Caption.
    lines.append(result.caption if result.caption else "_No caption._")
    lines.append("")

    # Media — embedded, sorted.
    lines.append("## Media")
    lines.append("")
    if media_files:
        for name in sorted(media_files):
            ext = _ext(name)
            if ext in IMAGE_EXTS:
                lines.append(f"![{name}]({name})")
            elif ext in VIDEO_EXTS:
                lines.append(f"[▶ Play video — {name}]({name})")
            else:
                lines.append(f"[{name}]({name})")
    else:
        lines.append("_No media files._")
    lines.append("")

    # Comments.
    lines.append(f"## Top {len(result.comments)} comments")
    lines.append("")
    if result.comments:
        for i, c in enumerate(result.comments, start=1):
            lines.append(f"{i}. **@{c.username}** (❤️ {c.likes:,}) — {c.text}")
    else:
        lines.append("_No comments returned._")
    lines.append("")

    return "\n".join(lines)


def _comment_dict(c: Comment) -> dict:
    return {
        "username": c.username,
        "likes": c.likes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "text": c.text,
    }


def render_metadata(result: ScrapeResult, media_files: list[str]) -> dict:
    """Render the JSON-serializable `metadata.json` companion."""
    return {
        "shortcode": result.shortcode,
        "source_url": result.source_url,
        "owner": result.owner,
        "typename": result.typename,
        "taken_at": result.taken_at.isoformat() if result.taken_at else None,
        "likes": result.likes,
        "is_video": result.is_video,
        "caption": result.caption,
        "media_files": media_files,
        "comments": [_comment_dict(c) for c in result.comments],
        "provenance": asdict(result.provenance) if result.provenance else None,
    }


def _rename_media(paths: list[Path], out_dir: Path, shortcode: str) -> list[str]:
    """Rename downloaded files to <shortcode>[.ext] / <shortcode>_<n>[.ext]."""
    names: list[str] = []
    single = len(paths) == 1
    for i, p in enumerate(paths, start=1):
        p = Path(p)
        ext = p.suffix.lower()
        new = out_dir / (f"{shortcode}{ext}" if single else f"{shortcode}_{i}{ext}")
        if p.resolve() != new.resolve():
            shutil.move(str(p), str(new))
        names.append(new.name)
    return names


def _download_cover(url, dest: Path) -> bool:
    try:
        urllib.request.urlretrieve(str(url), str(dest))
        return True
    except Exception:
        return False


def write_result(client, media, result: ScrapeResult, output_base: str, progress=None) -> Path:
    """Download all media for `media`, then write `post.md` + `metadata.json`.

    instagrapi downloads every media item — single image, reel video, or every
    carousel/album item — into `output_base/<shortcode>/`. For videos we also
    fetch the cover image so the Markdown has a visual preview.
    `progress` is an optional sink with a stage() method.
    """
    progress = progress or NullProgress()
    out_dir = Path(output_base) / result.shortcode
    out_dir.mkdir(parents=True, exist_ok=True)

    pk = media.pk
    kind = {1: "image", 2: "video", 8: "album"}.get(media.media_type, "media")
    progress.start(f"downloading {kind}")
    if media.media_type == 8:  # album / carousel
        downloaded = list(client.album_download(pk, folder=out_dir))
    elif media.media_type == 2:  # video / reel
        downloaded = [client.video_download(pk, folder=out_dir)]
    elif media.media_type == 1:  # photo
        downloaded = [client.photo_download(pk, folder=out_dir)]
    else:
        downloaded = []

    media_files = _rename_media([Path(p) for p in downloaded], out_dir, result.shortcode)

    # Cover image for a single video (Markdown can't inline-play video).
    if media.media_type == 2 and getattr(media, "thumbnail_url", None):
        cover = out_dir / f"{result.shortcode}.jpg"
        if not cover.exists() and _download_cover(media.thumbnail_url, cover):
            media_files.append(cover.name)

    media_files = sorted(set(media_files))
    progress.ok(f"{len(media_files)} file(s)")

    progress.start("writing post.md + metadata.json")
    (out_dir / "post.md").write_text(
        render_markdown(result, media_files), encoding="utf-8"
    )
    (out_dir / "metadata.json").write_text(
        json.dumps(render_metadata(result, media_files), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress.ok("done")
    return out_dir
