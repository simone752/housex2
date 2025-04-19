import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import json
import os
from dotenv import load_dotenv
import re
from jinja2 import Environment, FileSystemLoader

# Load .env credentials
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json'
MAX_SQUARE_METERS = 105
MAX_LISTING_AGE = timedelta(days=30)
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'corsica']

def connect_mail():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    print("✅ Connected to email")
    return mail

def load_listings():
    if not os.path.exists(LISTINGS_FILE) or os.path.getsize(LISTINGS_FILE) == 0:
        return []
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("⚠️ listings.json is invalid or corrupted. Starting fresh.")
        return []

def save_listings(listings):
    with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(listings, f, indent=2, ensure_ascii=False)

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
        # Extract info
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
            price_match = re.search(r'€\s*([\d\.]+)', price_tag.text)
            if price_match:
                data['price'] = float(price_match.group(1).replace('.', '').replace(',', '.'))

        data['location'] = data['name'].split(',')[-1].strip() if ',' in data['name'] else 'Unknown'

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

def rank_listings(listings):
    now = datetime.now(timezone.utc)
    for listing in listings:
        listing['price_per_sqm'] = listing['price'] / listing['square_meters']
        received_dt = datetime.fromisoformat(listing['received_time'])
        time_score = 1 / ((now - received_dt).total_seconds() / 3600 + 1)
        price_score = 1 / (listing['price_per_sqm'] + 1)
        listing['score'] = round(price_score * 0.7 + time_score * 0.3, 4)

    sorted_list = sorted(listings, key=lambda x: -x['score'])
    for i, listing in enumerate(sorted_list):
        listing['rank'] = i + 1
    return sorted_list

def render_website(listings):
    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('template.html')
    os.makedirs('docs', exist_ok=True)
    with open('docs/index.html', 'w', encoding='utf-8') as f:
        f.write(template.render(listings=listings))
    print("✅ Website updated at docs/index.html")

def scrape_immobiliare_emails():
    mail = None
    try:
        mail = connect_mail()
        mail.select('inbox')
        status, data = mail.search(None, '(FROM "noreply@notifiche.immobiliare.it")')
        if status != 'OK':
            print("No emails found.")
            return

        ids = data[0].split()
        listings = load_listings()
        existing_keys = {(l.get('name'), l.get('location'), l.get('square_meters')) for l in listings}
        new_count = 0

        for eid in ids:
            _, msg_data = mail.fetch(eid, '(RFC822)')
            raw_email = msg_data[0][1]
            message = email.message_from_bytes(raw_email, policy=policy.default)

            body = None
            if message.is_multipart():
                html = message.get_body(preferencelist=('html'))
                if html:
                    body = html.get_content()
            elif message.get_content_type() == 'text/html':
                body = message.get_content()

            if body:
                received_header = message.get('Date')
                parsed = utils.parsedate_tz(received_header)
                received_time = datetime.fromtimestamp(utils.mktime_tz(parsed), tz=timezone.utc).isoformat()

                new_item = parse_email(body, received_time)
                if new_item:
                    key = (new_item.get('name'), new_item.get('location'), new_item.get('square_meters'))
                    if all(key) and key not in existing_keys:
                        listings.append(new_item)
                        existing_keys.add(key)
                        new_count += 1

        if new_count > 0:
            print(f"✅ Found {new_count} new listings.")
            listings = rank_listings(listings)
            save_listings(listings)
            render_website(listings)
        else:
            print("ℹ️ No new listings added.")

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if mail:
            mail.logout()

if __name__ == '__main__':
    if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
        print("❌ Missing email credentials in .env")
    else:
        scrape_immobiliare_emails()
