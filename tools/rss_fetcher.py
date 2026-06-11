"""
tools/rss_fetcher.py

RSS feed fetching for podcast-style / live episode transcription.

Parses RSS 2.0 podcast feeds (and YouTube's Atom channel feeds) with
the stdlib only — no feedparser dependency.

Product rule: Social Spark Studio is designed for 5-20 minute episodes.
Episodes outside that range (when duration is known) are listed but
marked not selectable. Unknown durations are allowed with a warning.

v1 scope: audio enclosures only. Video enclosures are listed but
disabled ("audio extraction coming soon"). No MP4 clip cutting.
"""

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict

MIN_SECONDS = 5 * 60     # 5 minutes
MAX_SECONDS = 20 * 60    # 20 minutes
MAX_DOWNLOAD_MB = 50     # ~20 min of high-bitrate audio with headroom

_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_ATOM_NS   = "{http://www.w3.org/2005/Atom}"

_USER_AGENT = "SocialSparkStudio/1.0 (RSS transcript import)"


@dataclass
class RssEpisode:
    title: str = ""
    published: str = ""
    episode_link: str = ""
    media_url: str = ""
    media_type: str = ""
    duration_seconds: int = 0      # 0 = unknown
    selectable: bool = False
    reason: str = ""               # why not selectable (shown greyed out)
    warning: str = ""              # selectable but with a caveat

    def to_dict(self) -> dict:
        return asdict(self)


def _http_get(url: str, max_bytes: int = 5 * 1024 * 1024) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(max_bytes)


def parse_itunes_duration(raw: str) -> int:
    """Parse '1234', 'MM:SS', or 'HH:MM:SS' into seconds. 0 if unparseable."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    if re.fullmatch(r"\d+", raw):
        return int(raw)
    parts = raw.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def is_youtube_video_url(url: str) -> bool:
    """True for an individual YouTube video URL (not a feed)."""
    return bool(re.search(r"(?:youtube\.com/watch\?|youtu\.be/|youtube\.com/shorts/)", url or ""))


def _classify(ep: RssEpisode) -> None:
    """Apply the selectability rules in place."""
    if not ep.media_url:
        ep.selectable = False
        ep.reason = "No audio file found"
        return
    mt = (ep.media_type or "").lower()
    if mt.startswith("video/"):
        ep.selectable = False
        ep.reason = "Video file — audio extraction coming soon"
        return
    if mt and not mt.startswith("audio/"):
        ep.selectable = False
        ep.reason = "No audio file found"
        return
    if ep.duration_seconds:
        if ep.duration_seconds > MAX_SECONDS:
            ep.selectable = False
            ep.reason = "Too long for this tool"
            return
        if ep.duration_seconds < MIN_SECONDS:
            ep.selectable = False
            ep.reason = "Too short for this tool"
            return
    else:
        ep.warning = "Duration unknown — this tool works best with 5–20 minute episodes."
    ep.selectable = True


def fetch_feed_episodes(feed_url: str, limit: int = 15) -> dict:
    """
    Fetch and parse an RSS/Atom feed.

    Returns:
        {"success": bool, "feed_title": str, "episodes": [RssEpisode dicts],
         "error_message": str}
    """
    feed_url = (feed_url or "").strip()
    if not feed_url:
        return {"success": False, "error_message": "Please enter an RSS feed URL.", "episodes": []}

    if is_youtube_video_url(feed_url):
        return {
            "success": False,
            "error_message": "Please paste your RSS feed link, not an individual YouTube video URL.",
            "episodes": [],
        }

    try:
        raw = _http_get(feed_url)
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {
            "success": False,
            "error_message": "That URL did not return a valid RSS feed. "
                             "Look for a link called RSS Feed, Podcast RSS, or Copy RSS Link.",
            "episodes": [],
        }
    except Exception as exc:
        return {
            "success": False,
            "error_message": f"Could not fetch the feed: {type(exc).__name__}: {exc}",
            "episodes": [],
        }

    episodes: list[RssEpisode] = []
    feed_title = ""

    # ── RSS 2.0 (podcast standard) ─────────────────────────────────
    channel = root.find("channel")
    if channel is not None:
        feed_title = (channel.findtext("title") or "").strip()
        for item in channel.findall("item")[:limit]:
            enclosure = item.find("enclosure")
            ep = RssEpisode(
                title=(item.findtext("title") or "(untitled)").strip(),
                published=(item.findtext("pubDate") or "").strip(),
                episode_link=(item.findtext("link") or "").strip(),
                media_url=enclosure.get("url", "") if enclosure is not None else "",
                media_type=enclosure.get("type", "") if enclosure is not None else "",
                duration_seconds=parse_itunes_duration(
                    item.findtext(f"{_ITUNES_NS}duration") or ""
                ),
            )
            _classify(ep)
            episodes.append(ep)

    # ── Atom (e.g. YouTube channel feeds) ──────────────────────────
    elif root.tag == f"{_ATOM_NS}feed":
        feed_title = (root.findtext(f"{_ATOM_NS}title") or "").strip()
        for entry in root.findall(f"{_ATOM_NS}entry")[:limit]:
            link_el = entry.find(f"{_ATOM_NS}link")
            ep = RssEpisode(
                title=(entry.findtext(f"{_ATOM_NS}title") or "(untitled)").strip(),
                published=(entry.findtext(f"{_ATOM_NS}published") or "").strip(),
                episode_link=link_el.get("href", "") if link_el is not None else "",
                # Atom/YouTube feeds carry no audio enclosures
                media_url="",
                media_type="",
            )
            _classify(ep)   # → "No audio file found"
            episodes.append(ep)
    else:
        return {
            "success": False,
            "error_message": "Unrecognised feed format. Expected an RSS 2.0 podcast feed.",
            "episodes": [],
        }

    if not episodes:
        return {
            "success": False,
            "feed_title": feed_title,
            "error_message": "The feed loaded but contains no episodes.",
            "episodes": [],
        }

    return {
        "success": True,
        "feed_title": feed_title,
        "episodes": [e.to_dict() for e in episodes],
        "error_message": "",
    }


def build_transcript_result(
    chunks: list,
    media_url: str,
    episode_title: str = "",
    episode_link: str = "",
    language_code: str = "en-US",
    fallback_duration: float = 0.0,
):
    """
    Convert batch-STT chunks [{"text", "end_seconds"}] into the same
    TranscriptResult structure the YouTube fetcher produces, so the
    Moments → Posts → CSV pipeline continues unchanged.

    Segments are 40-120 words. clip_url uses the standard media-fragment
    syntax ({media_url}#t=SECONDS) — v1 keeps timestamp/source links;
    no MP4 clip cutting.

    chirp_3 batch responses often return one large chunk with no
    result_end_offset. When per-chunk timings are missing, the full text
    is split into ~80-word segments and timings are estimated evenly
    across fallback_duration (the episode duration from the RSS feed).
    """
    import hashlib
    from models.transcript_result import TranscriptSegment, TranscriptResult
    from tools.youtube_fetcher import seconds_to_timestamp

    has_timings = (
        len(chunks) > 1
        and any(c.get("end_seconds", 0) > 0 for c in chunks)
    )

    # Build (text, end_seconds) word groups
    word_groups: list[tuple[str, float]] = []
    if has_timings:
        for c in chunks:
            word_groups.append((c["text"], c.get("end_seconds", 0.0)))
        total_duration = max(c.get("end_seconds", 0.0) for c in chunks)
    else:
        # No usable timings — even split with estimated timings
        all_words = " ".join(c["text"] for c in chunks).split()
        total_duration = fallback_duration or 0.0
        target = 80   # words per segment
        n_words = len(all_words)
        for i in range(0, n_words, target):
            group_words = all_words[i:i + target]
            end_fraction = min((i + len(group_words)) / n_words, 1.0) if n_words else 1.0
            word_groups.append((" ".join(group_words), total_duration * end_fraction))

    segments = []
    buf_text: list[str] = []
    buf_words = 0
    seg_start = 0.0
    seg_index = 0
    prev_end = 0.0

    def flush(end_seconds: float):
        nonlocal buf_text, buf_words, seg_start, seg_index
        if not buf_text:
            return
        text = " ".join(buf_text)
        segments.append(TranscriptSegment(
            segment_index=seg_index,
            text=text,
            start_seconds=round(seg_start, 1),
            end_seconds=round(end_seconds, 1),
            start_timestamp=seconds_to_timestamp(seg_start),
            end_timestamp=seconds_to_timestamp(end_seconds),
            word_count=buf_words,
            clip_url=f"{media_url}#t={int(seg_start)}",
        ))
        seg_index += 1
        buf_text = []
        buf_words = 0
        seg_start = end_seconds

    for text, end_seconds in word_groups:
        buf_text.append(text)
        buf_words += len(text.split())
        prev_end = end_seconds
        if buf_words >= 40:
            flush(prev_end)
    flush(prev_end)   # remainder

    total_words = sum(s.word_count for s in segments)
    episode_id = hashlib.sha1(media_url.encode("utf-8")).hexdigest()[:11]

    return TranscriptResult(
        video_id=f"rss_{episode_id}",
        video_url=episode_link or media_url,
        language_code=language_code,
        is_generated=True,
        segments=segments,
        total_words=total_words,
        total_segments=len(segments),
        duration_seconds=total_duration,
        is_success=True,
    )


def download_enclosure(media_url: str, max_mb: int = MAX_DOWNLOAD_MB) -> bytes:
    """
    Download an episode's audio enclosure with a size cap.
    Raises ValueError when the file exceeds the cap.
    """
    req = urllib.request.Request(media_url, headers={"User-Agent": _USER_AGENT})
    max_bytes = max_mb * 1024 * 1024
    with urllib.request.urlopen(req, timeout=120) as resp:
        length = resp.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise ValueError(
                f"Audio file is {int(length) // (1024 * 1024)} MB — over the {max_mb} MB limit. "
                "This tool is designed for 5–20 minute episodes."
            )
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(
            f"Audio file exceeds the {max_mb} MB limit. "
            "This tool is designed for 5–20 minute episodes."
        )
    return data
