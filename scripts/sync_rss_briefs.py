import datetime
import email.utils
import html
import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

SITEMAP_SOURCES = [
    ("ShieldX Defense", "https://shieldxdefense.com", ["/intel/"]),
    ("Atomic Oversight", "https://atomicoversight.org", ["/intel/", "/reports/", "/briefings/", "/plume"]),
    ("WakeUp Nuclear", "https://wakeupnuclear.org", ["/reports/", "/benchmarks/"]),
    ("WakeUp Ballistic", "https://wakeupbulletproof.com", ["/briefings/"]),
]

SITEMAP_CANDIDATES = [
    "/sitemap-index.xml",
    "/sitemap-0.xml",
    "/sitemap.xml",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRIEFINGS_DIR = ROOT / "briefings"
README_PATH = ROOT / "README.md"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MAX_NEW_PER_RUN = 30


def fetch_bytes(url):
    try:
        res = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                "20",
                "-H",
                f"User-Agent: {USER_AGENT}",
                "-H",
                "Accept: text/html,application/xhtml+xml,application/xml,application/rss+xml,application/feed+json;q=0.9,*/*;q=0.8",
                url,
            ],
            capture_output=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout
    except FileNotFoundError:
        pass

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError):
        return None


def local_tag(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def strip_html(raw_text):
    if not raw_text:
        return ""
    text = html.unescape(raw_text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600] + ("..." if len(text) > 600 else "")


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:64] or "dispatch"


def parse_date(raw_date):
    if not raw_date:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    try:
        dt = email.utils.parsedate_to_datetime(raw_date)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", raw_date)
        if match:
            return match.group(1)
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def parse_sitemap_xml(xml_bytes):
    child_sitemaps = []
    urls = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return child_sitemaps, urls

    root_name = local_tag(root.tag)
    if root_name == "sitemapindex":
        for child in root:
            if local_tag(child.tag) == "sitemap":
                for sub in child:
                    if local_tag(sub.tag) == "loc" and sub.text:
                        child_sitemaps.append(sub.text.strip())
    elif root_name == "urlset":
        for child in root:
            if local_tag(child.tag) == "url":
                loc = ""
                lastmod = ""
                for sub in child:
                    name = local_tag(sub.tag)
                    if name == "loc" and sub.text:
                        loc = sub.text.strip()
                    elif name == "lastmod" and sub.text:
                        lastmod = sub.text.strip()
                if loc:
                    urls.append((loc, lastmod))
    return child_sitemaps, urls


def discover_sitemap_urls(base_url, prefixes):
    found = []
    visited_sitemaps = set()
    queue = [base_url.rstrip("/") + path for path in SITEMAP_CANDIDATES]

    while queue:
        sm_url = queue.pop(0)
        if sm_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sm_url)
        raw = fetch_bytes(sm_url)
        if not raw:
            continue
        children, urls = parse_sitemap_xml(raw)
        if children:
            for c in children:
                if c not in visited_sitemaps:
                    queue.append(c)
        if urls:
            for loc, lastmod in urls:
                if any(p in loc for p in prefixes):
                    found.append((loc, lastmod))
            break

    deduped = {}
    for loc, lastmod in found:
        norm = loc.rstrip("/")
        if norm not in deduped:
            deduped[norm] = (loc, lastmod)
    return list(deduped.values())


def extract_html_metadata(raw_bytes, fallback_url):
    text = raw_bytes.decode("utf-8", errors="ignore")
    title = ""
    desc = ""
    pub_date = ""

    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        text,
        re.IGNORECASE,
    )
    if og_title:
        title = html.unescape(og_title.group(1)).strip()
    if not title:
        t_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if t_match:
            title = strip_html(t_match.group(1))
    if not title:
        title = fallback_url.rstrip("/").split("/")[-1].replace("-", " ").title()

    for pat in (
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            desc = html.unescape(m.group(1)).strip()
            break

    d_match = re.search(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        text,
        re.IGNORECASE,
    )
    if d_match:
        pub_date = d_match.group(1).strip()

    return title, desc, pub_date


def parse_extra_rss_feeds():
    extra = os.environ.get("EXTRA_RSS_FEEDS", "").strip()
    if not extra:
        return []
    entries = []
    for idx, raw_url in enumerate(re.split(r"[\s,]+", extra), start=1):
        url = raw_url.strip()
        if not url.startswith("http"):
            continue
        raw = fetch_bytes(url)
        if not raw:
            print(f"[SKIP] Extra feed unreachable: {url}")
            continue
        source_name = "Substack Dispatch" if "substack.com" in url else f"External Feed {idx}"
        try:
            root = ET.fromstring(raw)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = strip_html(item.findtext("description") or "")
                pub_date = parse_date(item.findtext("pubDate") or "")
                if title and link.startswith("http"):
                    entries.append({
                        "source": source_name,
                        "title": html.unescape(title),
                        "link": link,
                        "summary": desc,
                        "date": pub_date,
                    })
        except ET.ParseError:
            print(f"[SKIP] Invalid XML in extra feed: {url}")
    return entries


def load_existing_urls():
    existing = set()
    if not BRIEFINGS_DIR.exists():
        return existing
    for path in BRIEFINGS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^\-\s+\*\*Canonical URL:\*\*\s+(.+)$", text, re.MULTILINE)
        if m:
            existing.add(m.group(1).strip().rstrip("/"))
    return existing


def write_briefing(entry):
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{entry['date']}-{slugify(entry['title'])}.md"
    target = BRIEFINGS_DIR / filename
    if target.exists():
        return filename, False

    content = (
        f"# {entry['title']}\n\n"
        f"- **Date:** {entry['date']}\n"
        f"- **Source:** {entry['source']}\n"
        f"- **Canonical URL:** {entry['link']}\n\n"
        f"## Executive Abstract\n\n"
        f"{entry['summary'] or 'Full technical telemetry and plume parameters are archived at the canonical source.'}\n\n"
        f"## Primary Reference Nodes\n\n"
        f"- [Full Technical Report]({entry['link']})\n"
        f"- [Atmospheric Plume Telemetry (EN)](https://atomicoversight.org/plume)\n"
        f"- [Atmospheric Plume Telemetry (AR)](https://atomicoversight.org/ar/plume)\n"
        f"- [Civilian Radiological Benchmarks](https://wakeupnuclear.org/benchmarks/civilian-radiation-suits-2026/)\n"
    )
    target.write_text(content, encoding="utf-8")
    return filename, True


def collect_index_rows():
    rows = []
    if not BRIEFINGS_DIR.exists():
        return rows
    for path in sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8")
        title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        date_m = re.search(r"^\-\s+\*\*Date:\*\*\s+(.+)$", text, re.MULTILINE)
        src_m = re.search(r"^\-\s+\*\*Source:\*\*\s+(.+)$", text, re.MULTILINE)
        url_m = re.search(r"^\-\s+\*\*Canonical URL:\*\*\s+(.+)$", text, re.MULTILINE)
        if title_m and url_m:
            rows.append({
                "date": date_m.group(1).strip() if date_m else path.name[:10],
                "source": src_m.group(1).strip() if src_m else "OSINT",
                "title": title_m.group(1).strip().replace("|", "-"),
                "file": f"briefings/{path.name}",
                "url": url_m.group(1).strip(),
            })
    return rows[:30]


def update_readme(rows):
    header = (
        "# D9 OSINT Group — Civil Continuity & Radiological Telemetry Archive\n\n"
        "Automated open-source intelligence archive indexing verified radiological safety, "
        "atmospheric plume modeling, and maritime/civil continuity dispatches.\n\n"
        "## Core Reference Endpoints\n\n"
        "- [Atomic Oversight — Atmospheric Plume Modeler](https://atomicoversight.org/plume)\n"
        "- [ShieldX Defense — Strategic Intelligence Briefings](https://shieldxdefense.com/intel)\n"
        "- [WakeUp Nuclear — Technical Attenuation & KI Limits](https://wakeupnuclear.org/reports/potassium-iodide-cs137-uf6-limits)\n"
        "- [WakeUp Ballistic — Convoy & Shrapnel Protocols](https://wakeupbulletproof.com/briefings/strait-of-hormuz-bahrain-airspace-convoy-ballistic-protocol)\n\n"
        "## Latest Archived Dispatches\n\n"
        "| Date | Source | Briefing | Canonical Link |\n"
        "| :--- | :--- | :--- | :--- |\n"
    )
    if rows:
        table_lines = [
            f"| {r['date']} | {r['source']} | [{r['title']}]({r['file']}) | [Source]({r['url']}) |"
            for r in rows
        ]
        body = "\n".join(table_lines) + "\n"
    else:
        body = "| Pending | System | Awaiting first scheduled sync | — |\n"

    README_PATH.write_text(header + body, encoding="utf-8")


def main():
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    existing_urls = load_existing_urls()
    created = 0

    for source_name, base_url, prefixes in SITEMAP_SOURCES:
        discovered = discover_sitemap_urls(base_url, prefixes)
        print(f"[INFO] {source_name} sitemap matched {len(discovered)} target URLs")
        for loc, lastmod in discovered:
            if created >= MAX_NEW_PER_RUN:
                break
            norm = loc.rstrip("/")
            if norm in existing_urls:
                continue
            page_bytes = fetch_bytes(loc)
            if not page_bytes:
                continue
            title, desc, html_date = extract_html_metadata(page_bytes, loc)
            pub_date = parse_date(html_date or lastmod)
            _, is_new = write_briefing({
                "source": source_name,
                "title": title,
                "link": loc,
                "summary": desc,
                "date": pub_date,
            })
            if is_new:
                existing_urls.add(norm)
                created += 1

    for entry in parse_extra_rss_feeds():
        norm = entry["link"].rstrip("/")
        if norm in existing_urls:
            continue
        _, is_new = write_briefing(entry)
        if is_new:
            existing_urls.add(norm)
            created += 1

    rows = collect_index_rows()
    update_readme(rows)
    print(f"Sync complete. New briefings written: {created}. Total indexed: {len(rows)}.")


if __name__ == "__main__":
    main()
