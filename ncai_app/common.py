import re
import socket
from typing import Any


def normalize_text(text: str) -> str:
    if not text:
        return ""

    normalized = text.strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[ㅋㅎㅠㅜ]{4,}", "", normalized)
    return normalized.strip()


def validate_user_text(text: str) -> bool:
    return bool(text and len(text.strip()) >= 2)


def clamp_score(score: int) -> int:
    return max(0, min(score, 100))


def clamp_subscore(score: int, max_value: int) -> int:
    return max(0, min(score, max_value))


def split_forwarded_for(header_value: str | None) -> list[str]:
    raw = normalize_text(header_value or "")
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def extract_client_ip_info(request: Any) -> dict[str, Any]:
    cf_connecting_ip = normalize_text(request.headers.get("CF-Connecting-IP", ""))
    x_forwarded_for = split_forwarded_for(request.headers.get("X-Forwarded-For"))
    remote_addr = normalize_text(getattr(request, "remote_addr", "") or "")

    if cf_connecting_ip:
        ip = cf_connecting_ip
        source = "cf-connecting-ip"
    elif x_forwarded_for:
        ip = x_forwarded_for[0]
        source = "x-forwarded-for"
    else:
        ip = remote_addr
        source = "remote-addr"

    return {
        "ip": ip or "unknown",
        "source": source,
        "forwarded_chain": x_forwarded_for,
        "remote_addr": remote_addr or "unknown",
        "cf_connecting_ip": cf_connecting_ip or "",
    }


def safe_reverse_dns(ip_address: str) -> str:
    ip = normalize_text(ip_address)
    if not ip or ip == "unknown":
        return ""

    try:
        hostname, *_ = socket.gethostbyaddr(ip)
        return normalize_text(hostname)
    except OSError:
        return ""


def infer_browser(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    browser_patterns = [
        ("edg/", "Microsoft Edge"),
        ("opr/", "Opera"),
        ("samsungbrowser/", "Samsung Internet"),
        ("whale/", "Naver Whale"),
        ("chrome/", "Google Chrome"),
        ("crios/", "Google Chrome (iOS)"),
        ("firefox/", "Mozilla Firefox"),
        ("fxios/", "Mozilla Firefox (iOS)"),
        ("safari/", "Safari"),
    ]

    for token, label in browser_patterns:
        if token in ua:
            return label

    return "Unknown Browser"


def infer_operating_system(user_agent: str, platform_hint: str = "") -> str:
    text = f"{user_agent} {platform_hint}".lower()

    os_patterns = [
        ("windows", "Windows"),
        ("android", "Android"),
        ("iphone", "iPhone"),
        ("ipad", "iPad"),
        ("ios", "iOS"),
        ("mac os", "macOS"),
        ("macintosh", "macOS"),
        ("linux", "Linux"),
    ]

    for token, label in os_patterns:
        if token in text:
            return label

    return "Unknown OS"


def infer_device_type(
    user_agent: str,
    is_mobile_hint: bool | None = None,
    max_touch_points: int | None = None,
) -> str:
    ua = (user_agent or "").lower()

    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if is_mobile_hint is True:
        return "mobile"
    if "iphone" in ua or "android" in ua or "mobile" in ua:
        return "mobile"
    if max_touch_points and max_touch_points >= 5 and "macintosh" in ua:
        return "tablet"
    return "desktop"


def build_device_name(
    browser: str,
    operating_system: str,
    hostname: str = "",
    model: str = "",
) -> str:
    normalized_model = normalize_text(model)
    normalized_hostname = normalize_text(hostname)

    if normalized_model:
        return normalized_model
    if normalized_hostname:
        return normalized_hostname
    if browser != "Unknown Browser" and operating_system != "Unknown OS":
        return f"{browser} on {operating_system}"
    if browser != "Unknown Browser":
        return browser
    if operating_system != "Unknown OS":
        return operating_system
    return "Unknown Device"
