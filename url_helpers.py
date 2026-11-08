"""URL parsing and site allowlisting for the Telegram video bot."""
import re
from urllib.parse import urlsplit

DEFAULT_DOMAINS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "instagr.am", "tiktok.com",
    "x.com", "twitter.com", "reddit.com", "redd.it", "v.redd.it",
    "facebook.com", "fb.watch", "vimeo.com", "dailymotion.com",
    "twitch.tv", "clips.twitch.tv", "streamable.com", "bsky.app",
}
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\[\]{}\"'`]+", re.IGNORECASE)



def csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def canonical_url(candidate: str) -> str | None:
    candidate = candidate.strip().rstrip(".,;:!?)]}>\u201d\u2019")
    if candidate.startswith("www."):
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 80, 443)
        ):
            return None
    except ValueError:
        return None
    return candidate


def host_allowed(url: str, domains: set[str]) -> bool:
    candidate = canonical_url(url)
    if not candidate:
        return False
    host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def extract_urls(message: object | None) -> list[str]:
    """Includes both visible URLs and hidden Telegram text links."""
    if not message:
        return []
    found: list[str] = []
    for caption in (False, True):
        try:
            entities = message.parse_caption_entities() if caption else message.parse_entities()
        except (AttributeError, ValueError):
            entities = {}
        for entity, text in entities.items():
            if entity.type == "text_link" and entity.url:
                found.append(entity.url)
            elif entity.type == "url":
                found.append(text)
    text = "\n".join(filter(None, (message.text, message.caption)))
    found.extend(URL_PATTERN.findall(text))
    output: list[str] = []
    for item in found:
        normalized = canonical_url(item)
        if normalized and normalized not in output:
            output.append(normalized)
    return output
