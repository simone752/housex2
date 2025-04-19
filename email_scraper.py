import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re

# Load environment variables from a .env file
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
LISTINGS_FILE = 'listings.json'

# Keywords to filter out bad listings (e.g., auctions, rentals)
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'corsica', 'monolocale', 'mansarda']

MAX_SQUARE_METERS = 105
MAX_LISTING_AGE = timedelta(days=20)

def connect_mail():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("Successfully connected to IMAP server.")
        return mail
    except imaplib.IMAP4.error as e:
        print(f"Error connecting to IMAP server: {e}")
        raise

def load_listings():
    if not os.path.exists(LISTINGS_FILE):
        return []
    if os.path.getsize(LISTINGS_FILE) == 0:
        return []
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            listings = json.load(f)
            return listings if isinstance(listings, list) else []
    except Exception:
        return []

def save_listings(listings):
    try:
        with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving listings: {e}")

def parse_email(body, received_time):
    soup = BeautifulSoup(body, 'html.parser')
    data = {
        'name': 'Unnamed',
        'square_meters': None,
        'location': 'Unknown',
        'price': None,
        'link': None,
        'extracted_time': datetime.now().isoformat(),
        'received_time': received_time
    }
    try:
        # Link and Name
        name_tag = soup.find('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1'))
        if name_tag:
            data['name'] = name_tag.text.strip()
            data['link'] = name_tag['href']

        # Square Meters
        sqm_tag = soup.find('td', class_='realEstateBlock__features')
        if sqm_tag:
            sqm_match = re.search(r'(\d+)\s*m²', sqm_tag.text)
            if sqm_match:
                data['square_meters'] = int(sqm_match.group(1))

        # Price
        price_tag = soup.find('td', class_='realEstateBlock__price')
        if price_tag:
            price_match = re.search(r'€\s*([\d\.]+)', price_tag.text)
            if price_match:
                data['price'] = float(price_match.group(1).replace('.', '').replace(',', '.'))

        # Location (fallback to name if needed)
        data['location'] = data['name'].split(',')[-1].strip() if ',' in data['name'] else 'Unknown'

        # Filter by keywords
        if any(bad_word in data['name'].lower() for bad_word in BAD_KEYWORDS):
            return None

        # Check size and age filters
        if not data['square_meters'] or not data['price']:
            return None
        if data['square_meters'] > MAX_SQUARE_METERS:
            return None

        # Check age
        received_dt = datetime.fromisoformat(received_time)
        if datetime.now() - received_dt > MAX_LISTING_AGE:
            return None

        return data

    except Exception as e:
        print(f"Error parsing email: {e}")
        return None

def rank_listings(listings):
    current_time = datetime.now()
    for listing in listings:
        listing['price_per_sqm'] = listing['price'] / listing['square_meters'] if listing['square_meters'] else float('inf')
        received_time = datetime.fromisoformat(listing['received_time'])
        listing['time_delta'] = (current_time - received_time).total_seconds()

        # Scoring: lower price per sqm and newer listings are better
        price_score = 1 / (listing['price_per_sqm'] + 1)
        time_score = 1 / (listing['time_delta'] / 3600 + 1)
        listing['score'] = round(price_score * 0.6 + time_score * 0.4, 4)

    sorted_list = sorted(listings, key=lambda x: -x['score'])
    for i, listing in enumerate(sorted_list):
        listing['rank'] = i + 1
    return sorted_list

def scrape_immobiliare_emails():
    mail = None
    try:
        mail = connect_mail()
        mail.select('inbox')
        status, data = mail.search(None, '(FROM "noreply@notifiche.immobiliare.it")')
        if status != 'OK' or not data or not data[0].strip():
            print("No emails found.")
            return

        ids = data[0].split()
        listings = load_listings()
        existing_keys = {(l.get('name'), l.get('location'), l.get('square_meters')) for l in listings}
        new_listings_found = 0

        for email_id in ids:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                continue
            raw_email = msg_data[0][1]
            message = email.message_from_bytes(raw_email, policy=policy.default)
            body = None
            if message.is_multipart():
                html_part = message.get_body(preferencelist=('html'))
                if html_part:
                    body = html_part.get_content()
            elif message.get_content_type() == 'text/html':
                body = message.get_content()

            if body:
                received_time = message.get('Date')
                parsed_time = utils.parsedate_tz(received_time)
                if parsed_time:
                    received_time = datetime.fromtimestamp(utils.mktime_tz(parsed_time)).isoformat()

                new_listing_data = parse_email(body, received_time)
                if new_listing_data:
                    listing_key = (new_listing_data.get('name'), new_listing_data.get('location'), new_listing_data.get('square_meters'))
                    if all(v is not None for v in listing_key) and listing_key not in existing_keys:
                        listings.append(new_listing_data)
                        existing_keys.add(listing_key)
                        new_listings_found += 1

        if new_listings_found > 0:
            listings = rank_listings(listings)
            save_listings(listings)
            print(f"Found {new_listings_found} new listings.")
        else:
            print("No new listings added.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception as e:
                print(f"Logout error: {e}")

if __name__ == '__main__':
    if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
        print("EMAIL_ACCOUNT or EMAIL_PASSWORD missing.")
    else:
        scrape_immobiliare_emails()
