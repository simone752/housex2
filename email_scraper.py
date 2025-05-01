import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re

# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
LISTINGS_FILE = 'listings.json'
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'Corsica', 'corsica', 'mansarda', 'villaggio']
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=30)
MIN_PRICE_PER_SQM = 1700


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


def extract_listings_from_email(body, received_time):
    soup = BeautifulSoup(body, 'html.parser')
    results = []

    # IMMOBILIARE.IT listings
    immo_tags = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1'))
    for tag in immo_tags:
        listing = {
            'name': tag.text.strip(),
            'link': tag['href'],
            'square_meters': None,
            'price': None,
            'location': 'Unknown',
            'extracted_time': datetime.now(timezone.utc).isoformat(),
            'received_time': received_time
        }

        parent = tag.find_parent('td')
        if parent:
            features = parent.find_next('td', class_='realEstateBlock__features')
            if features:
                sqm_match = re.search(r'(\d+)\s*m²', features.text)
                if sqm_match:
                    listing['square_meters'] = int(sqm_match.group(1))

            price_tag = parent.find_next('td', class_='realEstateBlock__price')
            if price_tag:
                price_text = price_tag.text.replace('.', '').replace(',', '.')
                price_match = re.search(r'€\s*([\d\.]+)', price_text)
                if price_match:
                    listing['price'] = float(price_match.group(1))

        results.append(listing)

    # CASA.IT listings
    casa_tags = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'), style=re.compile(r'color:\s*#1A1F24'))
    for tag in casa_tags:
        listing = {
            'name': tag.text.strip(),
            'link': tag['href'],
            'square_meters': None,
            'price': None,
            'location': 'Unknown',
            'extracted_time': datetime.now(timezone.utc).isoformat(),
            'received_time': received_time
        }

        parent = tag.find_parent()
        size_tag = parent.find_next('span', style=re.compile(r'padding-right:\s*10px'))
        if size_tag:
            sqm_match = re.search(r'(\d+)', size_tag.text)
            if sqm_match:
                listing['square_meters'] = int(sqm_match.group(1))

        price_tag = parent.find_next('span', style=re.compile(r'font-weight:\s*bold'))
        if price_tag:
            price_text = price_tag.text.replace('.', '').replace(',', '.')
            price_match = re.search(r'(\d+)', price_text)
            if price_match:
                listing['price'] = float(price_match.group(1))

        results.append(listing)

    return results


def validate_listing(listing):
    name = listing['name'].lower()

    if any(bad in name for bad in BAD_KEYWORDS):
        print(f"⚠️ Skipped (bad keyword): {listing['name']}")
        return False
    if not listing['square_meters']:
        print(f"⚠️ Skipped (missing square meters): {listing['name']}")
        return False
    if not listing['price']:
        print(f"⚠️ Skipped (missing price): {listing['name']}")
        return False
    if listing['square_meters'] > MAX_SQUARE_METERS:
        print(f"⚠️ Skipped (too big): {listing['name']} - {listing['square_meters']} sqm")
        return False
    if listing['square_meters'] < MIN_SQUARE_METERS:
        print(f"⚠️ Skipped (too small): {listing['name']} - {listing['square_meters']} sqm")
        return False

    price_per_sqm = listing['price'] / listing['square_meters']
    if price_per_sqm < MIN_PRICE_PER_SQM:
        print(f"⚠️ Skipped (too cheap per sqm): {listing['name']} - {price_per_sqm:.2f} €/m²")
        return False

    received_dt = datetime.fromisoformat(listing['received_time'])
    if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
        print(f"⚠️ Skipped (too old): {listing['name']}")
        return False

    if ',' in listing['name']:
        listing['location'] = listing['name'].split(',')[-1].strip()
    elif 'in' in listing['name']:
        listing['location'] = listing['name'].split('in')[-1].strip()

    print(f"✅ Valid listing: {listing['name']}")
    return True


def compute_score(listings):
    prices = [l['price'] / l['square_meters'] for l in listings]
    times = [datetime.fromisoformat(l['received_time']).timestamp() for l in listings]

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    for i, listing in enumerate(listings):
        price_per_sqm = prices[i]
        timestamp = times[i]

        norm_price = (price_per_sqm - min_price) / (max_price - min_price) if max_price != min_price else 0
        norm_time = (timestamp - min_time) / (max_time - min_time) if max_time != min_time else 1

        score = 0.5 * (1 - norm_price) + 0.5 * norm_time
        listing['score'] = score

    return sorted(listings, key=lambda x: -x['score'])


def scrape_listings():
    mail = connect_mail()
    mail.select('inbox')

    status, data = mail.search(None, '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")')
    email_ids = data[0].split()
    print(f"📥 Found {len(email_ids)} emails to process")

    listings = load_listings()
    seen_names = {l['name'] for l in listings}

    for eid in email_ids[::-1]:
        status, msg_data = mail.fetch(eid, '(RFC822)')
        if status != 'OK':
            continue

        msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
        sender = msg['From']
        subject = msg['Subject']
        print(f"\n📧 Email from: {sender} | Subject: {subject}")
        received_time = utils.parsedate_to_datetime(msg['Date']).astimezone(timezone.utc).isoformat()

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()

        new_listings = extract_listings_from_email(body, received_time)
        for listing in new_listings:
            if listing['name'] not in seen_names and validate_listing(listing):
                listings.append(listing)
                seen_names.add(listing['name'])
            else:
                print(f"⚠️ Duplicate or invalid: {listing['name']}")

    listings = compute_score(listings)
    save_listings(listings)
    print(f"\n✅ Done. Total listings saved: {len(listings)}")


if __name__ == '__main__':
    scrape_listings()


































































































































