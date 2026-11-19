import datetime
import email.utils
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_FEEDS = [
    ("ShieldX Defense", "https://shieldxdefense.com/rss.xml"),
    ("Atomic Oversight", "https://atomicoversight.org/rss.xml"),
]

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRIEFINGS_DIR = ROOT / "briefings"
README_PATH = ROOT / "README.md"
USER_AGENT = "D9-OSINT-Archiver/1.0 (+https://github.com/d9-osint-group)"


def load_feeds():
    feeds = list(DEFAULT_FEEDS)
    extra = os.environ.get("EXTRA_RSS_FEEDS", "").strip()
    if extra:
        for idx, raw_url in enumerate(re.split(r"[\s,]+", extra), start=1):
            url = raw_url.strip()
            if url.startswith("http"):
                label = "Substack Dispatch" if "substack.com" in url else f"External Feed {idx}"
                feeds.append((label, url))
    return feeds


def fetch_xml(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Skip {url}: {exc}")
        return None


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


def parse_feed(source_name, xml_bytes):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"XML parse error for {source_name}: {exc}")
        return items

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = strip_html(item.findtext("description") or "")
        pub_date = parse_date(item.findtext("pubDate") or "")
        if title and link:
            items.append({
                "source": source_name,
                "title": html.unescape(title),
                "link": link,
                "summary": desc,
                "date": pub_date,
            })

    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
            summary = strip_html(
                entry.findtext("atom:summary", namespaces=ns)
                or entry.findtext("atom:content", namespaces=ns)
                or ""
            )
            pub_date = parse_date(
                entry.findtext("atom:published", namespaces=ns)
                or entry.findtext("atom:updated", namespaces=ns)
                or ""
            )
            if title and link:
                items.append({
                    "source": source_name,
                    "title": html.unescape(title),
                    "link": link,
                    "summary": summary,
                    "date": pub_date,
                })

    return items


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
    return rows[:25]


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
        body = "| Pending | System | Awaiting first scheduled RSS sync | — |\n"

    README_PATH.write_text(header + body, encoding="utf-8")


def main():
    created = 0
    for source_name, url in load_feeds():
        xml_bytes = fetch_xml(url)
        if not xml_bytes:
            continue
        for entry in parse_feed(source_name, xml_bytes):
            _, is_new = write_briefing(entry)
            if is_new:
                created += 1
    rows = collect_index_rows()
    update_readme(rows)
    print(f"Sync complete. New briefings written: {created}. Total indexed: {len(rows)}.")


if __name__ == "__main__":
    main()
