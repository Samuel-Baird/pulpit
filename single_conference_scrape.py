"""Scrape General Conference talks into Markdown files.

Install dependencies locally with:
    pip install ftfy beautifulsoup4 requests trafilatura

`trafilatura` is optional. If it is not installed, the script falls back to a
basic paragraph-based extractor.
"""

import argparse
import calendar
import os
import re
import shutil
import zipfile
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from ftfy import fix_text

try:
    import trafilatura
except ImportError:
    trafilatura = None

CONFERENCE_URL = "https://www.churchofjesuschrist.org/study/general-conference/2025/10?lang=eng"
COLLECTED_BY = "Samuel Baird"
COLLECTED_DATE = "2026-03-23"
OUTPUT_EXT = ".md"
OUTPUT_FOLDER = "conference_output"
ZIP_NAME = "general_conference_md_files.zip"

HEADERS = {"User-Agent": "Mozilla/5.0"}

def clean_inline(text):
    if not text:
        return ""
    text = fix_text(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

def clean_block_text(text):
    if not text:
        return ""
    text = fix_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def slugify(text):
    text = clean_inline(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")

def short_title_slug(title):
    words = re.findall(r"[A-Za-z0-9']+", clean_inline(title).lower())
    stop = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with"}
    words = [w for w in words if w not in stop] or words
    return "-".join(words[:6]) if words else "untitled"

def speaker_filename(name):
    if not name:
        return "unknown-unknown"
    name = re.sub(r"\b(Elder|President|Bishop|Sister|Brother)\b\.?", "", clean_inline(name))
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        return "unknown-unknown"
    return f"{slugify(parts[-1])}-{slugify(parts[0])}"

def infer_date(url, session):
    m = re.search(r"/general-conference/(\d{4})/(\d{2})/", url)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    cal = calendar.monthcalendar(year, month)
    sat = next(week[calendar.SATURDAY] for week in cal if week[calendar.SATURDAY])
    sun = sat + 1
    s = (session or "").lower()

    if "saturday" in s:
        return f"{year}-{month:02d}-{sat:02d}"
    if "sunday" in s:
        return f"{year}-{month:02d}-{sun:02d}"

    # Fallback assumptions for named special sessions
    if "priesthood" in s or "women" in s or "young women" in s or "relief society" in s:
        return f"{year}-{month:02d}-{sat:02d}"

    return None

def q(x):
    return "null" if x is None else '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'

def get_soup(url):
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    return BeautifulSoup(res.text, "html.parser"), res.text

def find_top_title_and_conference(soup):
    h1 = soup.find("h1")
    title = clean_inline(h1.get_text(" ", strip=True)) if h1 else "Untitled"

    conference = None

    if h1:
        nearby = []
        for el in h1.find_all_next(["p", "div", "span", "a"], limit=12):
            txt = clean_inline(el.get_text(" ", strip=True))
            if not txt:
                continue
            if txt == title:
                continue
            nearby.append(txt)

        for txt in nearby:
            if "general conference" in txt.lower() and len(txt.split()) <= 6:
                conference = txt
                break

        if not conference:
            for txt in nearby[:4]:
                if "conference" in txt.lower():
                    conference = txt
                    break

    if conference:
        conference = clean_inline(conference)
        if len(conference.split()) > 8:
            conference = None

    return title, conference

def find_speaker(soup):
    for el in soup.find_all(["p", "div", "span"]):
        txt = clean_inline(el.get_text(" ", strip=True))
        if txt.lower().startswith("by "):
            txt = txt.split("\n")[0]
            txt = re.split(r"(?i)\bacting president\b|\bof the quorum\b", txt)[0].strip()
            return clean_inline(txt[3:])
    return None

def find_session_from_nav(soup, title):
    session_patterns = [
        "Saturday Morning Session",
        "Saturday Afternoon Session",
        "Saturday Evening Session",
        "Sunday Morning Session",
        "Sunday Afternoon Session",
        "Priesthood Session",
        "Women's Session",
        "Young Women Session",
        "Relief Society Session",
    ]

    texts = []
    for el in soup.find_all(["a", "div", "span", "p", "li"]):
        txt = clean_inline(el.get_text(" ", strip=True))
        if txt:
            texts.append(txt)

    talk_index = None
    for i, txt in enumerate(texts):
        if txt == title:
            talk_index = i
            break

    if talk_index is None:
        return None

    for j in range(talk_index - 1, -1, -1):
        for sess in session_patterns:
            if texts[j].lower() == sess.lower():
                return sess

    for j in range(talk_index - 1, -1, -1):
        if "session" in texts[j].lower():
            return texts[j]

    return None

def extract_transcript(html_text, source_title, conference, speaker, session):
    raw_text = None
    if trafilatura is not None:
        raw_text = trafilatura.extract(
            html_text,
            include_comments=False,
            include_tables=False,
            include_links=False,
            include_images=False,
            favor_precision=True,
            output_format="txt"
        )

    if not raw_text:
        soup = BeautifulSoup(html_text, "html.parser")
        paragraphs = []

        for p in soup.find_all("p"):
            txt = clean_inline(p.get_text(" ", strip=True))
            if not txt:
                continue
            paragraphs.append(txt)

        if not paragraphs:
            raise RuntimeError("Could not extract article text.")

        raw_lines = paragraphs
    else:
        raw_text = clean_block_text(raw_text)
        raw_lines = [clean_inline(x) for x in raw_text.split("\n") if clean_inline(x)]

    start_idx = 0
    for i, line in enumerate(raw_lines):
        if line == source_title:
            start_idx = i + 1
            break

    transcript_lines = []
    for line in raw_lines[start_idx:]:
        low = line.lower()

        if low in {"notes", "related content", "footnotes"}:
            break
        if line == source_title:
            continue
        if conference and line == conference:
            continue
        if speaker and (line == speaker or line == f"By {speaker}"):
            continue
        if session and line == session:
            continue
        if re.match(r"^\d+\.", line):
            break

        transcript_lines.append(line)

    while transcript_lines and (
        transcript_lines[0].lower().startswith("by ")
        or transcript_lines[0] == speaker
        or transcript_lines[0] == conference
        or transcript_lines[0] == session
    ):
        transcript_lines.pop(0)

    transcript = "\n\n".join(transcript_lines).strip()

    if not transcript:
        raise RuntimeError("Transcript came back empty after cleaning.")

    return transcript

def extract_talk_links(conference_url):
    soup, _ = get_soup(conference_url)
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin("https://www.churchofjesuschrist.org", href)

        if "/study/general-conference/" not in full:
            continue

        if re.search(r"/study/general-conference/\d{4}/\d{2}/[^/?#]+", full):
            if "lang=" not in full:
                full += "&lang=eng" if "?" in full else "?lang=eng"
            links.append(full)

    seen = set()
    unique = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    filtered = []
    for u in unique:
        if u.rstrip("/") == conference_url.rstrip("/"):
            continue

        slug_match = re.search(r"/study/general-conference/\d{4}/\d{2}/([^/?#]+)", u)
        if not slug_match:
            continue

        slug = slug_match.group(1)

        # Keep talk-like pages such as 13holland, 54eyring, etc.
        if re.match(r"^\d+[a-z-]+$", slug):
            filtered.append(u)

    return filtered

def scrape_talk(url):
    soup, html_text = get_soup(url)

    source_title, conference = find_top_title_and_conference(soup)
    speaker = find_speaker(soup)
    session = find_session_from_nav(soup, source_title)

    if not conference:
        m = re.search(r"/general-conference/(\d{4})/(\d{2})/", url)
        if m:
            year = m.group(1)
            month_num = int(m.group(2))
            month_name = calendar.month_name[month_num]
            conference = f"{month_name} {year} General Conference"

    if not speaker:
        m = re.search(r"/study/general-conference/\d{4}/\d{2}/([^/?#]+)", url)
        if m:
            slug = re.sub(r"^\d+", "", m.group(1))
            speaker = slug.replace("-", " ").title()

    date_val = infer_date(url, session) or "UNKNOWN-DATE"
    transcript = extract_transcript(html_text, source_title, conference, speaker, session)

    yaml_text = f"""---
# === IDENTIFICATION ===
speaker: {q(speaker)}
date: {q(date_val)}
conference: {q(conference)}
session: {q(session)}                     # e.g., "Saturday Morning", "Priesthood", "Sunday Afternoon"

# === SOURCE & PROVENANCE ===
source_title: {q(source_title)}
source_url: {q(url)}
source_type: "church_website"
# source_type options:
#   original_manuscript    — handwritten minutes or notes by an attendee
#   shorthand_transcription — transcribed from Pitman shorthand (JD, Deseret News pre-1870s)
#   newspaper_report       — published in Deseret News, Millennial Star, Times and Seasons
#   official_report        — from the official Conference Report series (1880, 1897+)
#   church_website         — from churchofjesuschrist.org (1971+)
#   compiled_transcription — from Watson or similar secondary compiler

fidelity: "verbatim"
# fidelity options:
#   verbatim        — believed to faithfully represent what was said (e.g., post-1942 official reports, modern digital text)
#   near_verbatim   — stenographic but with minor editorial polish (e.g., 1897-1942 Conference Reports)
#   edited          — significant editorial changes from original speech (e.g., most JD entries)
#   summary         — not a full transcript; a summary or synopsis of what was said
#   reconstructed   — compiled from fragments, journals, or secondary accounts
#   normalized      — transcript exists but spelling/grammar was modernized (e.g., Watson)

fidelity_notes: "Copied from the official Church website."

# === ALTERNATE SOURCES (optional, add as many as apply) ===
alternate_sources: []

# === COLLECTION METADATA ===
collected_by: {q(COLLECTED_BY)}
collected_date: {q(COLLECTED_DATE)}
needs_review: true                # set false once a second person has verified
notes: "No notes."
---

{transcript}
"""

    filename = f"{date_val}_{speaker_filename(speaker)}_{short_title_slug(source_title)}{OUTPUT_EXT}"
    return filename, yaml_text

def build_parser():
    parser = argparse.ArgumentParser(
        description="Download General Conference talks as Markdown files."
    )
    parser.add_argument(
        "--conference-url",
        default=CONFERENCE_URL,
        help="Conference page URL to scrape."
    )
    parser.add_argument(
        "--output-folder",
        default=OUTPUT_FOLDER,
        help="Folder for generated markdown files."
    )
    parser.add_argument(
        "--zip-name",
        default=ZIP_NAME,
        help="Name of the zip archive to create."
    )
    parser.add_argument(
        "--collected-by",
        default=COLLECTED_BY,
        help="Collector name written into the front matter."
    )
    parser.add_argument(
        "--collected-date",
        default=COLLECTED_DATE,
        help="Collection date written into the front matter."
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip creating the zip archive."
    )
    return parser


def maybe_download_in_colab(path):
    try:
        from google.colab import files
    except ImportError:
        return False

    files.download(path)
    return True


def main():
    global COLLECTED_BY, COLLECTED_DATE, OUTPUT_FOLDER

    args = build_parser().parse_args()
    COLLECTED_BY = args.collected_by
    COLLECTED_DATE = args.collected_date
    OUTPUT_FOLDER = args.output_folder

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    if trafilatura is None:
        print("Note: 'trafilatura' is not installed; using fallback HTML paragraph extraction.")

    talk_links = extract_talk_links(args.conference_url)
    print(f"Found {len(talk_links)} talk links")

    saved_files = []
    failed = []

    for i, link in enumerate(talk_links, start=1):
        try:
            filename, content = scrape_talk(link)
            path = os.path.join(OUTPUT_FOLDER, filename)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            saved_files.append(path)
            print(f"[{i}/{len(talk_links)}] Saved: {filename}")

        except Exception as e:
            failed.append((link, str(e)))
            print(f"[{i}/{len(talk_links)}] FAILED: {link}")
            print("   ", e)

    zip_path = os.path.abspath(args.zip_name)
    if not args.no_zip:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in saved_files:
                zf.write(path, arcname=os.path.basename(path))

    print()
    print(f"Saved {len(saved_files)} files")
    print(f"Failed {len(failed)} files")

    if failed:
        print("\nFailures:")
        for link, err in failed:
            print("-", link)
            print(" ", err)

    if not args.no_zip:
        print(f"Zip archive: {zip_path}")
        if not maybe_download_in_colab(zip_path):
            print("Colab download not available; zip file was created locally.")

    if shutil.which("python") is None:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
