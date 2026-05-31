import httpx, json
from selectolax.parser import HTMLParser
from loguru import logger

USER_AGENTS = [
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Twitterbot/1.0",
    "facebookexternalhit/1.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

async def fetch(url: str) -> dict:
    for ua in USER_AGENTS:
        try:
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
            }
            async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
                r = await client.get(url, headers=headers)
            result = _parse(r.text, url)
            if result.get("title"):
                logger.info(f"Metadata OK ua={ua[:30]} url={url}")
                return result
        except Exception as e:
            logger.warning(f"Metadata attempt failed ua={ua[:30]}: {e}")
            continue
    logger.warning(f"All metadata attempts failed for {url}")
    return {}

def _parse(html: str, url: str) -> dict:
    tree = HTMLParser(html)
    result = {}

    for tag in tree.css("meta"):
        prop = tag.attributes.get("property", "")
        name = tag.attributes.get("name", "")
        content = tag.attributes.get("content", "")
        if not content:
            continue
        if prop == "og:title":
            result["title"] = content
        elif prop == "og:description":
            result["description"] = content
        elif prop == "og:image":
            result["image"] = content
        elif prop == "og:site_name":
            result["site_name"] = content
        elif name == "description" and "description" not in result:
            result["description"] = content

    if "title" not in result:
        node = tree.css_first("title")
        if node:
            result["title"] = node.text(strip=True)

    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") in ("Product", "product"):
                if "name" in data and "title" not in result:
                    result["title"] = data["name"]
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                price = offers.get("price")
                currency = offers.get("priceCurrency", "")
                if price:
                    result["price"] = f"{price} {currency}".strip()
                brand = data.get("brand", {})
                if isinstance(brand, dict):
                    result["brand"] = brand.get("name", "")
                rating = data.get("aggregateRating", {})
                if rating:
                    rv = rating.get("ratingValue")
                    rc = rating.get("reviewCount")
                    if rv:
                        result["rating"] = f"⭐ {rv}"
                        if rc:
                            result["rating"] += f" \\({rc} відгуків\\)"
        except Exception:
            continue

    return result

def _escape(text: str) -> str:
    """Экранирование спецсимволов для MarkdownV2."""
    chars = r"_*[]()~`>#+-=|{}.!"
    for ch in chars:
        text = text.replace(ch, f"\\{ch}")
    return text

def format_card(meta: dict) -> str:
    """Карточка без ссылки, с экранированием для MarkdownV2."""
    lines = []
    if meta.get("site_name"):
        lines.append(f"🌐 {_escape(meta['site_name'])}")
    if meta.get("title"):
        lines.append(f"📌 *{_escape(meta['title'])}*")
    if meta.get("brand"):
        lines.append(f"🏷 {_escape(meta['brand'])}")
    if meta.get("price"):
        lines.append(f"💰 *{_escape(str(meta['price']))}*")
    if meta.get("rating"):
        lines.append(meta["rating"])
    if meta.get("description"):
        desc = meta["description"]
        if len(desc) > 200:
            desc = desc[:200].rsplit(" ", 1)[0] + "…"
        lines.append(f"\n{_escape(desc)}")
    return "\n".join(lines)
