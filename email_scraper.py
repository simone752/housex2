import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
import re
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json'
BAD_KEYWORDS = ['stazione', 'asta', 'affitto']
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=30)
MIN_PRICE_PER_SQM = 1700

def connect_to_mail():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("✅ Connected to email")
        return mail
    except Exception as e:
        print(f"❌ Error connecting: {e}")
        raise

def load_existing_listings():
    if os.path.exists(LISTINGS_FILE):
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_listings(listings):
    with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(listings, f, indent=2, ensure_ascii=False)

def parse_immobiliare(soup, received_time):
    results = []
    for block in soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/')):
        listing = {
            'name': block.get_text(strip=True),
            'link': block['href'],
            'square_meters': None,
            'price': None,
            'location': '',
            'received_time': received_time,
            'extracted_time': datetime.now(timezone.utc).isoformat()
        }
        try:
            parent = block.find_parent('td')
            features = parent.find_next('td', class_='realEstateBlock__features')
            if features:
                match = re.search(r'(\d+)\s*m²', features.text)
                if match:
                    listing['square_meters'] = int(match.group(1))

            price_block = parent.find_next('td', class_='realEstateBlock__price')
            if price_block:
                price_text = price_block.text.replace('.', '').replace(',', '.')
                price_match = re.search(r'€\s*([\d\.]+)', price_text)
                if price_match:
                    listing['price'] = float(price_match.group(1))
        except Exception as e:
            print(f"⚠️ Parsing immobiliare listing failed: {e}")
        results.append(listing)
    return results

def parse_casait(soup, received_time):
    results = []
    for block in soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/')):
        listing = {
            'name': block.get_text(strip=True),
            'link': block['href'],
            'square_meters': None,
            'price': None,
            'location': '',
            'received_time': received_time,
            'extracted_time': datetime.now(timezone.utc).isoformat()
        }
        try:
            parent = block.find_parent()
            size_tag = parent.find_next('span', style=re.compile(r'padding-right:\s*10px'))
            if size_tag:
                sqm_match = re.search(r'(\d+)', size_tag.text)
                if sqm_match:
                    listing['square_meters'] = int(sqm_match.group(1))

            price_tag = parent.find_next('span', style=re.compile(r'font-weight:\s*bold'))
            if price_tag:
                price_text = price_tag.text.replace('.', '').replace(',', '.')
                price_match = re.search(r'(\d+)')
                if price_match:
                    listing['price'] = float(price_match.group(0))
        except Exception as e:
            print(f"⚠️ Parsing casa.it listing failed: {e}")
        results.append(listing)
    return results

def validate_listing(listing):
    if not listing['name'] or not listing['square_meters'] or not listing['price']:
        return False

    name = listing['name'].lower()
    if any(bad in name for bad in BAD_KEYWORDS):
        return False

    if listing['square_meters'] < MIN_SQUARE_METERS or listing['square_meters'] > MAX_SQUARE_METERS:
        return False

    if listing['price'] / listing['square_meters'] < MIN_PRICE_PER_SQM:
        return False

    received_dt = datetime.fromisoformat(listing['received_time'])
    if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
        return False

    if ',' in listing['name']:
        listing['location'] = listing['name'].split(',')[-1].strip()
    elif 'in' in listing['name']:
        listing['location'] = listing['name'].split('in')[-1].strip()

    return True

def compute_scores(listings):
    prices = [l['price'] / l['square_meters'] for l in listings]
    times = [datetime.fromisoformat(l['received_time']).timestamp() for l in listings]

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    for i, l in enumerate(listings):
        price_per_sqm = prices[i]
        timestamp = times[i]
        norm_price = (price_per_sqm - min_price) / (max_price - min_price) if max_price != min_price else 0
        norm_time = (timestamp - min_time) / (max_time - min_time) if max_time != min_time else 1
        l['score'] = round(0.6 * (1 - norm_price) + 0.4 * norm_time, 4)

    listings.sort(key=lambda l: -l['score'])
    return listings

def scrape_emails():
    mail = connect_to_mail()
    mail.select('inbox')

    status, data = mail.search(None, '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")')
    if status != 'OK':
        print("❌ Failed to search mailbox")
        return

    listings = load_existing_listings()
    seen = {l['link'] for l in listings}

    for eid in reversed(data[0].split()):
        _, msg_data = mail.fetch(eid, '(RFC822)')
        msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
        received_time = utils.parsedate_to_datetime(msg['Date']).astimezone(timezone.utc).isoformat()

        html_body = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    html_body = part.get_content()
                    break
        else:
            html_body = msg.get_content()

        if not html_body:
            continue

        soup = BeautifulSoup(html_body, 'html.parser')
        new_listings = parse_immobiliare(soup, received_time) + parse_casait(soup, received_time)

        for l in new_listings:
            if l['link'] not in seen and validate_listing(l):
                listings.append(l)
                seen.add(l['link'])

    listings = compute_scores(listings)
    save_listings(listings)
    print(f"✅ Saved {len(listings)} listings")

if __name__ == '__main__':
    scrape_emails()





