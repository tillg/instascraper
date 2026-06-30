# insta_scraper

Archive an Instagram **post** or **reel** from its URL into a self-contained
folder: all media (images + videos, including carousels), the caption, and the
top 10 comments, rendered as a readable `post.md` plus a `metadata.json`.

```
output/DXOCAyzEX8i/
├── post.md          # caption + embedded media + top 10 comments
├── DXOCAyzEX8i.mp4  # the reel video
├── DXOCAyzEX8i.jpg  # the video cover
└── metadata.json    # raw fields + provenance
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .          # installs deps + the `instascrape` command
```

This puts an `instascrape` executable in `.venv/bin/`, so after `activate` you
can just run `instascrape …` (`insta-scraper` is a kept alias). On a
pyenv-shimmed shell, a `python` function can shadow the venv interpreter — using
the `instascrape` command or `./.venv/bin/python -m insta_scraper …` avoids that.

## Login & config

The tool authenticates as **you** and **persists the session**, so it logs in
only once. Later runs reuse the saved session (no password, works
non-interactively) and re-login only if the session dies — reusing the same
device identity each time so Instagram doesn't flag a "new device" on every run.

The first run takes your username + password; it then saves a durable session to
`~/.config/insta_scraper/session-<username>.json`, and (unless `--no-save-config`)
remembers your credentials and options in `~/.config/insta_scraper/.env`
(chmod 600) so you can omit them next time. If Instagram asks for a 2FA /
security-challenge code (email or SMS), you'll be prompted for it.

```bash
# First run — pass credentials once; they're saved for next time:
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --username tillg --password 'xyz'

# Afterwards just pass URLs — username, password and options come from config:
instascrape "https://www.instagram.com/reel/DZ_KsKvKAW0/"
instascrape --file SAMPLE_URLS.md --target-dir data --delay 8
```

Option precedence: **CLI flag > saved config (`.env`) > environment variable >
built-in default**. Credentials never need to be re-typed once stored.

**Alternative: import a browser session** (one-shot bootstrap; less durable —
browser-imported sessions tend to get flagged by Instagram sooner):

```bash
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/" --browser safari
# (also: chrome, brave, edge, firefox, chromium, opera, vivaldi)
```

## Usage

```bash
# Single URL
instascrape "https://www.instagram.com/reel/DXOCAyzEX8i/"

# Batch every Instagram URL found in a file, into data/, paced:
instascrape --file SAMPLE_URLS.md --target-dir data --delay 8
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--username NAME` | stored config | Instagram account to log in as (saved) |
| `--password PW` | stored config | Password — only needed for the first login (saved) |
| `--target-dir DIR` | `output` | Base directory for the per-post folders (alias: `--output`) |
| `--session-file PATH` | `~/.config/insta_scraper/session-<user>.json` | Where the session is stored/reused |
| `--browser NAME` | off | Bootstrap login from a logged-in browser (safari, chrome, …) |
| `--delay SECONDS` | `3` | Pause between items in batch mode |
| `--comment-sort {likes,instagram}` | `likes` | Ranking rule for the top 10 (see below) |
| `--comment-scan-limit N` | `200` | Comments to scan before ranking; `0` = all (slow, rate-limit risk) |
| `--no-save-config` | off | Don't write credentials/options to the config file |

All saved options live in `~/.config/insta_scraper/.env`. `instascrape -h` shows
everything. Exit codes: `0` all good · `1` some items skipped · `2` fatal
(auth / rate limit — stopped early).

## About "top 10 comments"

Instagram's in-app "top comments" ranking is algorithmic and **not** exposed;
`get_comments()` returns latest-first. So "top" here is a *constructed*
measurement — by default, the 10 comments with the highest like count among the
first 200 scanned. Every `post.md` states the exact rule it used in its
provenance header, so the export is honest about what "top" means. Use
`--comment-sort instagram` for first-returned order instead.

## Notes & limitations

- **Personal use.** Automated collection is against Instagram's Terms of
  Service. This tool is for **personal archival** of content you can already see
  while logged in — it authenticates as you and does not bypass access controls,
  CAPTCHAs, or rate limits.
- **Personal data / EU-GDPR.** An export contains other people's usernames,
  comment text, and timestamps. Keeping a private archive is one thing;
  **republishing or sharing** it raises data-protection and copyright
  obligations and is out of scope for this tool.
- **Out of scope:** Stories/Highlights, whole-profile or hashtag crawls, comment
  replies/threads, any GUI.
- **Fallback:** if Instaloader's comment ordering or media coverage ever falls
  short, the scraper backend can be swapped to `instagrapi` without changing the
  CLI or output format (see `specs/changes/initial_scraper/architecture.md`).

## Development

```bash
pip install -r requirements.txt
python -m pytest          # network-free unit tests
```
