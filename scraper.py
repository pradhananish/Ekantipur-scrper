import json
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Browser, Error, Locator, Page, TimeoutError, expect, sync_playwright

BASE_URL = "https://ekantipur.com"
OUTPUT_FILE = "output.json"


def normalize_url(url: str | None, base_url: str = BASE_URL) -> str | None:
    """Normalize relative URLs into absolute URLs."""
    if not url:
        return None
    return urljoin(base_url, url.strip())


def safe_inner_text(locator: Locator) -> str | None:
    """Return stripped text from the first matched element, or None."""
    try:
        if locator.count() == 0:
            return None
        text = locator.first.inner_text().strip()
        return text or None
    except Error:
        return None


def safe_attribute(locator: Locator, attr_name: str) -> str | None:
    """Return attribute value from the first matched element, or None."""
    try:
        if locator.count() == 0:
            return None
        value = locator.first.get_attribute(attr_name)
        return value.strip() if value else None
    except Error:
        return None


def dismiss_pagegate_modal(page: Page) -> None:
    """Dismiss common homepage modal/backdrop overlays that block clicks."""
    try:
        modal = page.locator("#pagegate")
        if modal.count() > 0:
            close_btn = page.locator(
                "#pagegate button.close, "
                "#pagegate .close, "
                "#pagegate [data-bs-dismiss='modal'], "
                "#pagegate [aria-label='Close']"
            ).first
            if close_btn.count() > 0:
                close_btn.click(timeout=2_000, force=True)

        page.keyboard.press("Escape")
        page.evaluate(
            """
            () => {
              const modal = document.querySelector('#pagegate');
              if (modal) {
                modal.classList.remove('show');
                modal.setAttribute('aria-hidden', 'true');
                modal.style.display = 'none';
              }
              document
                .querySelectorAll('.modal-backdrop, .custom-modal-backdrop')
                .forEach((el) => el.remove());
              document.body.classList.remove('modal-open');
              document.body.style.removeProperty('overflow');
              document.body.style.removeProperty('padding-right');
            }
            """
        )
    except Error:
        # Non-fatal: continue even if overlay handling is not needed/possible.
        pass


def open_homepage(page: Page) -> bool:
    """Open the homepage and wait for key load states."""
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=20_000)
        dismiss_pagegate_modal(page)
        return True
    except TimeoutError as exc:
        print(f"Homepage load timed out: {exc}")
        return False


def open_entertainment_section(page: Page) -> bool:
    """Navigate to the मनोरञ्जन section from homepage."""
    try:
        dismiss_pagegate_modal(page)

        # Prefer click-through navigation; fallback to direct URL.
        entertainment_link = page.locator(
            "a[href='https://ekantipur.com/entertainment'], a[href='/entertainment']"
        ).first

        if entertainment_link.count() > 0:
            expect(entertainment_link).to_be_visible(timeout=15_000)
            try:
                entertainment_link.click(timeout=4_000)
            except (TimeoutError, Error):
                dismiss_pagegate_modal(page)
                try:
                    entertainment_link.click(timeout=4_000, force=True)
                except (TimeoutError, Error):
                    page.goto(f"{BASE_URL}/entertainment", wait_until="domcontentloaded", timeout=20_000)
        else:
            page.goto(f"{BASE_URL}/entertainment", wait_until="domcontentloaded", timeout=20_000)

        page.wait_for_load_state("domcontentloaded", timeout=20_000)
        page.wait_for_load_state("networkidle", timeout=20_000)
        current_url = page.url.strip()
        if "/entertainment" not in current_url:
            print(f"Unexpected URL after navigation: {current_url}")
            return False
        return True
    except (TimeoutError, Error) as exc:
        print(f"Failed to open मनोरञ्जन section: {exc}")
        return False


def get_entertainment_section_label(page: Page) -> str | None:
    """Extract the section label text from navigation for category field."""
    return safe_inner_text(
        page.locator("a[href='https://ekantipur.com/entertainment'], a[href='/entertainment']")
    )


def enrich_from_article_page(browser: Browser, url: str) -> dict[str, str | None]:
    """Fetch missing metadata (author/image/category) from article page."""
    detail_page = browser.new_page()
    try:
        detail_page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        detail_page.wait_for_load_state("networkidle", timeout=10_000)

        image_url = normalize_url(
            safe_attribute(detail_page.locator("meta[property='og:image']"), "content")
            or safe_attribute(detail_page.locator("article img"), "src")
            or safe_attribute(detail_page.locator("img"), "src")
        )
        author = (
            safe_inner_text(detail_page.locator(".author-name, [rel='author'], [itemprop='author']"))
            or safe_attribute(detail_page.locator("meta[name='author']"), "content")
        )
        category = (
            safe_inner_text(detail_page.locator(".category, .cat, .breadcrum a:last-child, .breadcrumb a:last-child"))
            or safe_attribute(detail_page.locator("meta[property='article:section']"), "content")
        )

        return {
            "image_url": image_url,
            "author": author,
            "category": category,
        }
    except (TimeoutError, Error):
        return {
            "image_url": None,
            "author": None,
            "category": None,
        }
    finally:
        detail_page.close()


def extract_top_entertainment_news(page: Page, browser: Browser, limit: int = 5) -> list[dict[str, Any]]:
    """Extract top entertainment cards with complete fields when available."""
    results: list[dict[str, Any]] = []
    section_label = get_entertainment_section_label(page)

    # Entertainment listing currently uses `div.category` cards.
    cards = page.locator("div.category")
    total_cards = cards.count()

    for idx in range(total_cards):
        if len(results) >= limit:
            break

        card = cards.nth(idx)
        title = safe_inner_text(card.locator("h2"))
        link = normalize_url(safe_attribute(card.locator("h2 a"), "href"))
        image_url = normalize_url(
            safe_attribute(card.locator("img"), "src")
            or safe_attribute(card.locator("img"), "data-src")
        )
        author = safe_inner_text(card.locator(".author-name, [rel='author'], [itemprop='author']"))
        category = section_label

        # Skip unusable cards that do not represent a story.
        if not title or not link:
            continue

        # Best effort: backfill missing fields from the article page.
        if not image_url or not author or not category:
            enriched = enrich_from_article_page(browser, link)
            image_url = image_url or enriched["image_url"]
            author = author or enriched["author"]
            category = category or enriched["category"] or section_label

        results.append(
            {
                "title": title,
                "image_url": image_url,
                "category": category,
                "author": author,
                "link": link,
            }
        )

    return results


def extract_cartoon_of_the_day(page: Page) -> dict[str, Any] | None:
    """Extract cartoon metadata from homepage carousel section."""
    try:
        section = page.locator(
            "div.section-news:has(h4 a[href='https://ekantipur.com/cartoon']), "
            "div.section-news:has(h4 a[href='/cartoon'])"
        ).first
        if section.count() == 0:
            return None

        title = safe_inner_text(section.locator(".swiper-slide-active img"))
        image_url = normalize_url(
            safe_attribute(section.locator(".swiper-slide-active img"), "src")
            or safe_attribute(section.locator(".swiper-slide-active img"), "data-src")
            or safe_attribute(section.locator(".c-slide img"), "src")
            or safe_attribute(section.locator(".c-slide img"), "data-src")
        )
        link = normalize_url(
            safe_attribute(section.locator("h4 a[href*='/cartoon']"), "href")
            or safe_attribute(section.locator(".swiper-slide-active a"), "href")
        )

        return {
            "title": title or safe_inner_text(section.locator("h4")),
            "image_url": image_url,
            "link": link,
        }
    except Error:
        return None


def save_json(data: dict[str, Any], file_path: str = OUTPUT_FILE) -> None:
    """Write output JSON with Unicode support and readable formatting."""
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def run_scraper() -> None:
    """Run full scraping flow and save result to output.json."""
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            if not open_homepage(page):
                return

            cartoon_of_the_day = extract_cartoon_of_the_day(page)

            if not open_entertainment_section(page):
                return

            entertainment_news = extract_top_entertainment_news(page, browser, limit=5)

            payload = {
                "entertainment_news": entertainment_news,
                "cartoon_of_the_day": cartoon_of_the_day,
            }
            save_json(payload)
            print(f"Saved scraped data to {OUTPUT_FILE}")
        finally:
            browser.close()


if __name__ == "__main__":
    run_scraper()
