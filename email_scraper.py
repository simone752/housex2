import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re
import pandas as pd

# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
LISTINGS_FILE = 'listings.json'
OUTPUT_HTML = 'docs/index.html'

BAD_KEYWORDS = ['stazione', 'asta', 'affitto']
MAX_SQUARE_METERS = 105
MAX_LISTING_AGE = timedelta(days=30)


def connect_mail():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("✅ Connected to email")
        return mail
    except imaplib.IMAP4.error as e:
        print(f"❌ Connection error: {e}")
        raise


def load_listings():
    if not os.path.exists(LISTINGS_FILE) or os.path.getsize(LISTINGS_FILE) == 0:
        return []
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("⚠️ listings.json is invalid or corrupted. Starting fresh.")
        return []
    except Exception as e:
        print(f"❌ Error loading listings: {e}")
        return []


def save_listings(listings):
    try:
        with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error saving listings: {e}")


def parse_email(body, received_time):
    soup = BeautifulSoup(body, 'html.parser')
    data = {
        'name': 'Unnamed',
        'square_meters': None,
        'location': 'Unknown',
        'price': None,
        'link': None,
        'extracted_time': datetime.now(timezone.utc).isoformat(),
        'received_time': received_time
    }

    try:
        # IMMOBILIARE.IT
        name_tag = soup.find('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1'))
        if name_tag:
            data['name'] = name_tag.text.strip()
            data['link'] = name_tag['href']

            sqm_tag = soup.find('td', class_='realEstateBlock__features')
            if sqm_tag:
                sqm_match = re.search(r'(\d+)\s*m²', sqm_tag.text)
                if sqm_match:
                    data['square_meters'] = int(sqm_match.group(1))

            price_tag = soup.find('td', class_='realEstateBlock__price')
            if price_tag:
                price_text = price_tag.text.replace('.', '').replace(',', '.')
                price_match = re.search(r'€\s*([\d\.]+)', price_text)
                if price_match:
                    data['price'] = float(price_match.group(1))

        # CASA.IT
        casa_link_tag = soup.find('a', href=re.compile(r'https://www\.casa\.it/immobili/'), style=re.compile(r'color:\s*#1A1F24'))
        if casa_link_tag:
            data['name'] = casa_link_tag.text.strip()
            data['link'] = casa_link_tag['href']

            size_tag = soup.find('span', style=re.compile(r'padding-right:\s*10px'))
            if size_tag:
                size_match = re.search(r'(\d+)', size_tag.text)
                if size_match:
                    data['square_meters'] = int(size_match.group(1))

            price_tag = soup.find('span', style=re.compile(r'font-weight:bold'))
            if price_tag:
                price_text = price_tag.text.replace('.', '').replace(',', '.').strip()
                price_match = re.search(r'(\d+)', price_text)
                if price_match:
                    data['price'] = float(price_match.group(1))

        # Location from name
        if ',' in data['name']:
            data['location'] = data['name'].split(',')[-1].strip()
        elif 'in' in data['name']:
            data['location'] = data['name'].split('in')[-1].strip()

        # Apply filters
        if any(bad_word in data['name'].lower() for bad_word in BAD_KEYWORDS):
            return None
        if not data['square_meters'] or not data['price']:
            return None
        if data['square_meters'] > MAX_SQUARE_METERS:
            return None

        received_dt = datetime.fromisoformat(received_time)
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            return None

        return data

    except Exception as e:
        print(f"❌ Error parsing email: {e}")
        return None


def scrape_listings():
    mail = connect_mail()
    mail.select('inbox')

    status, data = mail.search(None, '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")')
    email_ids = data[0].split()

    listings = load_listings()
    seen_names = {l['name'] for l in listings}

    for e_id in email_ids[::-1]:  # newest first
        status, data = mail.fetch(e_id, '(RFC822)')
        if status != 'OK':
            continue

        msg = email.message_from_bytes(data[0][1], policy=policy.default)
        received_time = utils.parsedate_to_datetime(msg['Date']).astimezone(timezone.utc).isoformat()

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()

        listing = parse_email(body, received_time)
        if listing and listing['name'] not in seen_names:
            listings.append(listing)
            seen_names.add(listing['name'])

    save_listings(listings)
    print(f"✅ Saved {len(listings)} listings.")


if __name__ == '__main__':
    scrape_listings()

