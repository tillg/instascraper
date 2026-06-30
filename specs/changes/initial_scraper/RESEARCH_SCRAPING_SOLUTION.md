
A fellow agent did a reseaerch on existing soliutions for our use case. Here is what he found out.

Yes. For your exact use case, I’d start with instaloader. It can download Instagram photos/videos plus captions/metadata/comments, and it can create a Post object from the shortcode inside a post/Reel URL. Its docs show Post.from_shortcode() for URL shortcodes and Post.get_comments() for comment iteration.     It also has download_post(), which downloads the media attached to a post: picture, caption, and video.  

There is one important caveat: Instagram’s Terms prohibit collecting information in unauthorized ways, and Instagram can restrict accounts for automated collection. Use this only for content you own, have permission to archive, or can lawfully access with your logged-in account; do not build around CAPTCHA/rate-limit bypassing.    

Recommended architecture

Your backend receives the Instagram URL, extracts the shortcode, loads a saved Instagram session, downloads the media into a directory, then writes a post.md file with caption and comments.

Output shape:

instagram_archives/
  DXOCAyzEX8i/
    DXOCAyzEX8i_2026-...jpg
    DXOCAyzEX8i_2026-...mp4
    post.md

For a Reel, you normally get the .mp4 plus a thumbnail/cover image. For a carousel, you may get multiple image/video files.

Login: do not pass the password every time

With instaloader, the program can log in once and save a local session file. Its docs state that login stores a session cookie, not the password, and that --password is discouraged for security reasons.   It can also import browser cookies with --load-cookies, after which it saves the session for later runs.  

For a production app, I would not ask end users for Instagram passwords. Use the official Meta OAuth APIs where they cover your use case. If this is an internal/personal archiver, do a one-time interactive login and store the session file encrypted or in a protected server volume.

Minimal working Python script

Install:

python -m venv .venv
source .venv/bin/activate
pip install instaloader

Create archive_instagram_url.py:

#!/usr/bin/env python3
from __future__ import annotations
import argparse
import itertools
import os
import re
from pathlib import Path
from typing import Any
import instaloader
from instaloader import Post
from instaloader.exceptions import InstaloaderException
URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/"
    r"(?:p|reel|tv)/(?P<shortcode>[A-Za-z0-9_-]+)/?"
)
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
def shortcode_from_url(url: str) -> str:
    match = URL_RE.search(url)
    if not match:
        raise ValueError("Expected an Instagram /p/, /reel/, or /tv/ URL.")
    return match.group("shortcode")
def load_or_create_session(
    loader: instaloader.Instaloader,
    username: str,
    sessionfile: Path,
) -> None:
    sessionfile.parent.mkdir(parents=True, exist_ok=True)
    if sessionfile.exists():
        loader.load_session_from_file(username, str(sessionfile))
        if loader.test_login():
            return
    # Prompts for password and 2FA/challenge if needed.
    loader.interactive_login(username)
    loader.save_session_to_file(str(sessionfile))
def comment_to_dict(comment: Any) -> dict[str, Any]:
    owner = getattr(getattr(comment, "owner", None), "username", "unknown")
    created = getattr(comment, "created_at_utc", None)
    likes = getattr(comment, "likes_count", None)
    return {
        "username": owner,
        "created_at_utc": created.isoformat() if created else "",
        "likes": int(likes or 0),
        "text": getattr(comment, "text", ""),
    }
def fetch_comments(
    post: Post,
    top_n: int,
    comment_sort: str,
    comment_scan_limit: int,
) -> tuple[list[dict[str, Any]], str]:
    """
    comment_sort='instagram':
        Takes the first N comments returned by Instagram/Instaloader.
        This is not guaranteed to equal the UI's "Top comments".
    comment_sort='likes':
        Fetches comments, sorts by likes_count, and returns top N.
        Exact "top by likes" requires scanning all comments, which can be slow.
    """
    comments_iter = post.get_comments()
    if comment_sort == "likes":
        if comment_scan_limit > 0:
            comments_iter = itertools.islice(comments_iter, comment_scan_limit)
            note = f"Top {top_n} by likes among the first {comment_scan_limit} scanned comments."
        else:
            note = f"Top {top_n} by likes after scanning all available comments."
        comments = [comment_to_dict(c) for c in comments_iter]
        comments.sort(key=lambda c: c["likes"], reverse=True)
        return comments[:top_n], note
    note = (
        f"First {top_n} comments returned by Instagram/Instaloader; "
        "not guaranteed to match Instagram UI top-comment ranking."
    )
    return [comment_to_dict(c) for c in itertools.islice(comments_iter, top_n)], note
def write_markdown(
    outdir: Path,
    source_url: str,
    post: Post,
    comments: list[dict[str, Any]],
    comment_note: str,
) -> None:
    media_files = sorted(
        p.name
        for p in outdir.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
    )
    lines: list[str] = [
        "# Instagram archive",
        "",
        "## Post",
        "",
        f"- Source URL: `{source_url}`",
        f"- Shortcode: `{post.shortcode}`",
        f"- Owner: `@{post.owner_username}`",
        f"- Type: `{post.typename}`",
        f"- Created UTC: `{post.date_utc.isoformat()}`",
        f"- Likes: `{post.likes}`",
        f"- Reported comment count: `{post.comments}`",
    ]
    if post.is_video:
        lines.extend(
            [
                f"- Video duration seconds: `{post.video_duration}`",
                f"- Video view count: `{post.video_view_count}`",
                f"- Video play count: `{post.video_play_count}`",
            ]
        )
    lines.extend(["", "## Caption", "", post.caption or "_No caption._", ""])
    lines.extend(["## Media files", ""])
    if media_files:
        lines.extend(f"- `{name}`" for name in media_files)
    else:
        lines.append("_No media files found._")
    lines.extend(["", "## Comments", "", f"_Note: {comment_note}_", ""])
    if comments:
        for idx, comment in enumerate(comments, start=1):
            lines.extend(
                [
                    f"### {idx}. @{comment['username']}",
                    "",
                    f"- Created UTC: `{comment['created_at_utc']}`",
                    f"- Likes: `{comment['likes']}`",
                    "",
                    comment["text"] or "_Empty comment._",
                    "",
                ]
            )
    else:
        lines.append("_No comments returned._")
    (outdir / "post.md").write_text("\n".join(lines), encoding="utf-8")
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Instagram /p/, /reel/, or /tv/ URL")
    parser.add_argument("--out", default="instagram_archives", help="Output root directory")
    parser.add_argument(
        "--username",
        default=os.environ.get("IG_USERNAME"),
        help="Instagram username, or set IG_USERNAME",
    )
    parser.add_argument(
        "--sessionfile",
        default=None,
        help="Path to saved Instaloader session file",
    )
    parser.add_argument("--comments", type=int, default=10)
    parser.add_argument(
        "--comment-sort",
        choices=["instagram", "likes"],
        default="instagram",
        help="'instagram' = first returned; 'likes' = sort scanned comments by like count",
    )
    parser.add_argument(
        "--comment-scan-limit",
        type=int,
        default=200,
        help="Only for --comment-sort likes. Use 0 to scan all comments.",
    )
    args = parser.parse_args()
    if not args.username:
        raise SystemExit("Pass --username or set IG_USERNAME.")
    shortcode = shortcode_from_url(args.url)
    out_root = Path(args.out)
    outdir = out_root / shortcode
    outdir.mkdir(parents=True, exist_ok=True)
    sessionfile = (
        Path(args.sessionfile)
        if args.sessionfile
        else Path(".instagram_sessions") / f"{args.username}.session"
    )
    loader = instaloader.Instaloader(
        dirname_pattern=str(out_root / "{target}"),
        filename_pattern="{shortcode}_{date_utc}_UTC",
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=True,
        download_geotags=False,
        download_comments=False,       # We write our own top-10 markdown instead.
        save_metadata=False,           # Avoid extra JSON files.
        compress_json=False,
        post_metadata_txt_pattern="",  # Avoid extra caption .txt files.
        sanitize_paths=True,
    )
    try:
        load_or_create_session(loader, args.username, sessionfile)
        post = Post.from_shortcode(loader.context, shortcode)
        # Downloads images, video, and video thumbnail into out_root/<shortcode>/
        loader.download_post(post, target=shortcode)
        comments, note = fetch_comments(
            post=post,
            top_n=args.comments,
            comment_sort=args.comment_sort,
            comment_scan_limit=args.comment_scan_limit,
        )
        write_markdown(
            outdir=outdir,
            source_url=args.url,
            post=post,
            comments=comments,
            comment_note=note,
        )
        print(f"Archive written to: {outdir}")
    except InstaloaderException as exc:
        raise SystemExit(f"Instaloader/Instagram error: {exc}") from exc
if __name__ == "__main__":
    main()

Run it:

export IG_USERNAME="your_instagram_username"
python archive_instagram_url.py \
  "https://www.instagram.com/reel/DXOCAyzEX8i/" \
  --out instagram_archives

For “top by likes” instead of “first comments returned”:

python archive_instagram_url.py \
  "https://www.instagram.com/reel/DXOCAyzEX8i/" \
  --comment-sort likes \
  --comment-scan-limit 500

Use --comment-scan-limit 0 only when you really need exact top-by-likes across all comments, because that can trigger many requests on large posts.

“Top 10 comments” caveat

Instaloader.get_comments() iterates comments, but not necessarily in the same order as Instagram’s app UI. There is an old Instaloader issue noting that users wanted UI-style “top comments,” while Instaloader was pulling latest comments first.   The script above gives you two practical modes: first 10 returned, or sort by likes_count among a scanned set. Instaloader exposes likes_count and text on comment objects.  

Alternative library: instagrapi

instagrapi is another GitHub library. It is an unofficial Instagram private/mobile API wrapper with login, session persistence, media, comments, uploads, and related operations; its own README warns that private API automation is fragile and says to prefer official APIs for account-owned business workflows.   It has direct helpers such as media_pk_from_url(), media_comments(), media_comments_public_gql(), and comment pagination helpers.   It also has download helpers for photos, videos, albums, IGTV, and Reels clips.  

I would use:

instaloader  -> easiest archive/downloader workflow
instagrapi   -> more control over unofficial API calls and comment endpoints
official API -> best for production when you only need accounts/media your app is allowed to manage

Official API option

For production apps, Meta’s Instagram API with Facebook Login is the sanctioned path. It is for Instagram Professional accounts, meaning Business and Creator accounts, and supports managing media and comments on those accounts.   It cannot access consumer accounts, and ordering results is not supported.   The permissions shown in Meta’s Postman collection include instagram_basic, instagram_content_publish, and instagram_manage_comments.  

The official route is better if you are building a SaaS product. The unofficial scraping route is more suitable for a personal/internal archiver where you understand the account and compliance risk.