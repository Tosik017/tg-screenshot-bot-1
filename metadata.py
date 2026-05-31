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
    """Быстрый fetch через httpx — без браузера. Один запрос — первый успешный."""
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
            if result.get("title") and result["title"] not in ("", url):
                logger.info(f"Metadata OK ua={ua[:30]} url={url}")
                return result
        except Exception as e:
            logger.warning(f"Metadata attempt failed ua={ua[:30]}: {e}")
            continue
    logger.warning(f"All httpx attempts failed for {url}")
    return {}

def parse_from_html(html: str, url: str) -> dict:
    """Парсим HTML из Playwright после page.content()."""
    return _parse(html, url)

def _walk_jsonld(data):
    """Обходим все JSON-LD объекты включая @graph — фикс для Elmir, Rozetka, Comfy."""
    if isinstance(data, dict):
        yield data
        if "@graph" in data:
            for item in data["@graph"]:
                yield from _walk_jsonld(item)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_jsonld(item)

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
        elif name == "twitter:title" and "title" not in result:
            result["title"] = content
        elif name == "twitter:description" and "description" not in result:
            result["description"] = content
        elif name == "twitter:image" and "image" not in result:
            result["image"] = content
        elif name == "description" and "description" not in result:
            result["description"] = content

    if "title" not in result:
        node = tree.css_first("title")
        if node:
            result["title"] = node.text(strip=True)

    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
            for item in _walk_jsonld(data):
                if item.get("@type") in ("Product", "product"):
                    if "name" in item and "title" not in result:
                        result["title"] = item["name"]
                    offers = item.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0]
                    price = offers.get("price")
                    currency = offers.get("priceCurrency", "")
                    if price:
                        result["price"] = f"{price} {currency}".strip()
                    brand = item.get("brand", {})
                    if isinstance(brand, dict):
                        result["brand"] = brand.get("name", "")
                    rating = item.get("aggregateRating", {})
                    if rating:
                        rv = rating.get("ratingValue")
                        rc = rating.get("reviewCount")
                        if rv:
                            result["rating"] = f"⭐ {rv}"
                            if rc:
                                result["rating"] += f" ({rc} відгуків)"
                    break
        except Exception:
            continue

    return result
