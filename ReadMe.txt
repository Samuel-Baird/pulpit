# General Conference Scraper

This project scrapes General Conference talks from  
https://www.churchofjesuschrist.org/study/general-conference?lang=eng  
and converts them into structured `.md` files with metadata.

---

## 📦 Features

- Scrapes full talk transcripts
- Automatically organizes by conference (e.g., *October 2024*)
- Saves each talk as a clean Markdown file
- Includes metadata (speaker, date, session, source, etc.)
- Outputs a zipped archive of all scraped content

---

## ⚙️ Installation

Install required packages:

```bash
pip install -r requirements.txt


## Requirements 
trafilatura
ftfy
beautifulsoup4
requests
