#!/usr/bin/env python3
"""Watches a notice page and sends anything new to Telegram.

No extra packages needed (Python standard library only).

Environment variables:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather   (required unless DRY_RUN=1)
  TELEGRAM_CHAT_ID    your chat id                (required unless DRY_RUN=1)
  NOTICE_URL          page to watch (default: https://bisag-n.gov.in/notice)
  STATE_FILE          where seen items are stored (default: seen.json)
  DRY_RUN=1           print messages instead of sending them
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

URL = os.environ.get("NOTICE_URL", "https://bisag-n.gov.in/notice")
STATE = Path(os.environ.get("STATE_FILE", "seen.json"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MAX_LISTED = 15      # most new items shown in one alert
MAX_CHANGES = 40     # more than this at once = probably a layout change

BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "br", "section",
    "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
}
SKIP_TAGS = {"script", "style", "noscript"}


class PageParser(HTMLParser):
    """Collects every link (text + address) and every line of visible text."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.links = []
        self.lines = []
        self._skip = 0
        self._href = None
        self._link_text = []
        self._buf = []

    def _flush(self):
        line = " ".join(self._buf).strip()
        self._buf = []
        if len(line) >= 12:
            self.lines.append(line)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            href = href.strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self._href = urllib.parse.urljoin(self.base, href)
                self._link_text = []
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href:
            text = " ".join(self._link_text).strip()
            if text:
                self.links.append((text, self._href))
            self._href = None
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self._buf.append(text)
        if self._href is not None:
            self._link_text.append(text)

    def close(self):
        super().close()
        self._flush()


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (notice-watcher)"}
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def key_of(kind, *parts):
    return kind + ":" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def collect_items(html):
    parser = PageParser(URL)
    parser.feed(html)
    parser.close()

    items = {}  # key -> text to show in the alert
    link_texts = set()
    for text, href in parser.links:
        link_texts.add(text)
        items[key_of("L", text, href)] = f"{text}\n{href}"
    for line in parser.lines:
        if line not in link_texts:
            items[key_of("T", line)] = line
    return items


def telegram_send(text):
    if DRY_RUN:
        print("--- [DRY RUN] message ---")
        print(text)
        print("-------------------------")
        return
    api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": text[:4000], "disable_web_page_preview": "true"}
    ).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(api, data=data), timeout=30)
    except urllib.error.HTTPError as e:
        # Print only the reason, never the URL (it contains the token).
        print(f"Telegram error {e.code}: {e.read().decode(errors='replace')}")
        sys.exit(1)


def load_state():
    if STATE.exists():
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    return None


def save_state(keys):
    STATE.write_text(json.dumps(sorted(keys)), encoding="utf-8")


def main():
    if not DRY_RUN and not (TOKEN and CHAT_ID):
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID set nahi hain.")
        sys.exit(1)

    items = collect_items(fetch(URL))
    if not items:
        print("Page par koi link/text nahi mila (page JavaScript se load ho raha ho sakta hai).")
        sys.exit(1)

    seen = load_state()
    if seen is None:
        save_state(items.keys())
        telegram_send(
            f"✅ Notice watcher chalu ho gaya.\n"
            f"Abhi {len(items)} items track ho rahe hain. Ab se naya kuch aaya to yahin batayenge.\n{URL}"
        )
        print(f"Baseline saved: {len(items)} items")
        return

    new_keys = [k for k in items if k not in seen]
    if new_keys:
        if len(new_keys) > MAX_CHANGES:
            telegram_send(
                f"⚠️ Noticeboard page mein bahut saare badlav dikhe ({len(new_keys)}). "
                f"Shayad page ka layout badla hai.\n{URL}"
            )
        else:
            shown = [items[k] for k in new_keys[:MAX_LISTED]]
            extra = len(new_keys) - len(shown)
            body = "\n\n".join(f"• {s}" for s in shown)
            if extra > 0:
                body += f"\n\n...aur {extra} naye items"
            telegram_send(f"🔔 Noticeboard par naya update\n\n{body}\n\n{URL}")
        print(f"{len(new_keys)} new item(s) reported")
    else:
        print("Koi naya update nahi.")

    save_state(seen | set(items.keys()))


if __name__ == "__main__":
    main()
  
