import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
import re
import string # Import for punctuation removal
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import logging

# --- Configuration ---
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json' # OVERWRITTEN EACH RUN

# Filtering Criteria
BAD_KEYWORDS = ['asta', 'affitto', 'garage', 'box', 'ufficio', 'laboratorio', 'negozio', 'capannone'] # Added more non-residential
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=45) # Allow slightly older listings when rescanning all
MIN_PRICE_PER_SQM = 1700

# Scoring Weights
PRICE_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4

# Deduplication Setting
SIMILARITY_WORD_SEQUENCE = 5

# Email Search Query (Searches ALL matching emails)
# !!! IMPORTANT: Add Idealista sender if you want to parse those emails !!!
# Example: '(OR FROM "sender1" FROM "sender2" FROM "idealista_sender")'
EMAIL_SEARCH_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com" FROM "alerts@idealista.com")' # Added common idealista alert sender


# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---

def normalize_name(name):
    """Normalizes a listing name for comparison."""
    if not name: return []
    name = name.lower()
    name = name.replace('\ufeff', '') # Remove BOM character specifically
    name = name.translate(str.maketrans('', '', string.punctuation))
    return [word for word in name.split() if word]

def are_names_similar(name1, name2, min_sequence=SIMILARITY_WORD_SEQUENCE):
    """Checks if two names share a sequence of at least min_sequence words."""
    words1 = normalize_name(name1)
    words2 = normalize_name(name2)
    if not words1 or not words2 or len(words1) < min_sequence or len(words2) < min_sequence:
        return False
    ngrams1 = {tuple(words1[i:i + min_sequence]) for i in range(len(words1) - min_sequence + 1)}
    ngrams2 = {tuple(words2[i:i + min_sequence]) for i in range(len(words2) - min_sequence + 1)}
    return not ngrams1.isdisjoint(ngrams2)

def connect_to_mail():
    """Connects to the IMAP server."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        logging.info("✅ Connected to email account")
        return mail
    except Exception as e:
        logging.error(f"❌ Error connecting to email: {e}")
        raise

def save_listings(listings, filename=LISTINGS_FILE):
    """Saves listings to a JSON file (OVERWRITES)."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 SAVED (Overwritten) {len(listings)} listings to {filename}")
    except Exception as e:
        logging.error(f"❌ Error saving listings to {filename}: {e}")

def clean_text(text):
    """Utility to clean whitespace and remove specific artifacts."""
    if not text: return ""
    text = text.replace('\ufeff', '') # Remove BOM
    text = text.replace('\xa0', ' ') # Replace non-breaking space
    return ' '.join(text.split()) # Normalize whitespace

def extract_number(text, is_float=True):
    """Extracts the first number (int or float) from a string, handling common formats."""
    if not text: return None
    # Pre-processing: Remove "Da " prefix, currency symbols, thousands separators
    text = re.sub(r'^[^\d€]*', '', text).strip() # Remove leading non-digits/€
    text = text.replace('€', '').strip()
    text = text.replace('.', '').replace(',', '.') # Standardize decimal point

    # Find the first sequence of digits, possibly with a decimal point
    match = re.search(r'(\d[\d\.]*)', text)
    if match:
        num_str = match.group(1)
        try:
            # Remove trailing dot if it's not part of a decimal
            if num_str.endswith('.'): num_str = num_str[:-1]

            if is_float:
                return float(num_str)
            else:
                 # For integers like square meters, handle potential floats
                 return int(float(num_str))
        except ValueError:
            logging.debug(f"⚠️ Could not convert '{num_str}' to number from text: '{text}'")
            return None
    return None

# --- NEW / REWRITTEN Parsing Logic ---

def parse_casait(soup, received_time):
    """Parses listings from Casa.it email HTML based on provided example."""
    results = []
    # Find link tags first
    link_tags = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'), style=re.compile(r'color:\s*#1A1F24', re.IGNORECASE))
    logging.debug(f"[Casa.it] Found {len(link_tags)} potential link tags.")

    for link_tag in link_tags:
        listing = {'source': 'casa.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            # 1. Extract Link and Name
            listing['link'] = link_tag.get('href')
            listing['name'] = clean_text(link_tag.get_text(strip=True))

            if not listing['link'] or not listing['name']:
                 logging.debug("[Casa.it] Skipping item: Missing link or name.")
                 continue

            # Find a common ancestor container (heuristic, might need adjustment)
            # Go up a few levels, assuming link, price, size are near each other
            container = link_tag.find_parent('td') or link_tag.find_parent('div') or link_tag.parent

            if not container:
                 logging.warning(f"[Casa.it] Could not find container for link: {listing['link']}")
                 container = soup # Fallback to search whole soup if needed, less precise

            # 2. Extract Price (look for the specific styled span nearby)
            price_tag = container.find('span', style=re.compile(r'font-weight:\s*bold', re.IGNORECASE))
            if price_tag:
                listing['price'] = extract_number(price_tag.get_text(strip=True), is_float=True)
            else:
                logging.debug(f"[Casa.it] Price tag not found near {listing['link']}")

            # 3. Extract Size (look for the specific styled span nearby)
            size_tag = container.find('span', style=re.compile(r'padding-right:\s*10px', re.IGNORECASE))
            if size_tag:
                sqm_match = re.search(r'(\d+)\s*m', size_tag.get_text(strip=True), re.IGNORECASE) # Simpler match for sqm
                if sqm_match:
                    listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else:
                 logging.debug(f"[Casa.it] Size tag not found near {listing['link']}")

            # Only add if we have the essentials (Link, Name mandatory. Price, Size highly desirable)
            if listing['link'] and listing['name']: # Consider adding checks for price/sqm if needed
                 results.append(listing)
                 logging.debug(f"[Casa.it] Parsed: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else:
                 logging.debug(f"[Casa.it] Skipped partial data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"[Casa.it] Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False) # Set exc_info=True for trace

    logging.info(f"[Casa.it] Successfully parsed {len(results)} listings from this email.")
    return results

def parse_immobiliare(soup, received_time):
    """Parses listings from Immobiliare.it email HTML based on provided example."""
    results = []
    # Find link tags first
    link_tags = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1', re.IGNORECASE))
    logging.debug(f"[Immobiliare.it] Found {len(link_tags)} potential link tags.")

    for link_tag in link_tags:
        listing = {'source': 'immobiliare.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            # 1. Extract Link and Name
            listing['link'] = link_tag.get('href')
            listing['name'] = clean_text(link_tag.get_text(strip=True))

            if not listing['link'] or not listing['name']:
                 logging.debug("[Immobiliare.it] Skipping item: Missing link or name.")
                 continue

            # Find the parent TD, assuming structure based on example
            parent_td = link_tag.find_parent('td')
            if not parent_td:
                 # Fallback: If link is not in a TD, try finding TR and search within that row
                 parent_tr = link_tag.find_parent('tr')
                 if not parent_tr:
                     logging.warning(f"[Immobiliare.it] Could not find parent TD or TR for link: {listing['link']}")
                     continue # Skip if structure is unexpected
                 container = parent_tr
            else:
                container = parent_td.parent # Assume parent TD is within a TR, use TR as container


            # 2. Extract Price (find TD with specific class within the container)
            price_td = container.find('td', class_='realEstateBlock__price')
            if price_td:
                listing['price'] = extract_number(price_td.get_text(strip=True), is_float=True)
            else:
                 logging.debug(f"[Immobiliare.it] Price TD not found for {listing['link']}")


            # 3. Extract Size (find TD with specific class, then extract number + m²)
            features_td = container.find('td', class_='realEstateBlock__features')
            if features_td:
                sqm_match = re.search(r'(\d+)\s*m[²2q]', features_td.get_text(strip=True), re.IGNORECASE) # Look for mq, m2, m²
                if sqm_match:
                    listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else:
                 logging.debug(f"[Immobiliare.it] Features TD not found for {listing['link']}")

            # Only add if we have the essentials
            if listing['link'] and listing['name']:
                 results.append(listing)
                 logging.debug(f"[Immobiliare.it] Parsed: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else:
                 logging.debug(f"[Immobiliare.it] Skipped partial data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"[Immobiliare.it] Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False)

    logging.info(f"[Immobiliare.it] Successfully parsed {len(results)} listings from this email.")
    return results

def parse_idealista(soup, received_time):
    """Parses listings from Idealista.it email HTML based on provided example."""
    results = []
     # Find link tags first (adjust selector if needed)
    link_tags = soup.find_all('a', href=re.compile(r'https://www\.idealista\.it/immobile/'), style=re.compile(r'color:\s*#2172b2', re.IGNORECASE))
    logging.debug(f"[Idealista] Found {len(link_tags)} potential link tags.")

    for link_tag in link_tags:
        listing = {'source': 'idealista.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            # 1. Extract Link and Name
            listing['link'] = link_tag.get('href')
            listing['name'] = clean_text(link_tag.get_text(strip=True))

            if not listing['link'] or not listing['name']:
                 logging.debug("[Idealista] Skipping item: Missing link or name.")
                 continue

            # Find a common ancestor container (heuristic)
            container = link_tag.find_parent('td') or link_tag.find_parent('div') or link_tag.parent
            if not container:
                 logging.warning(f"[Idealista] Could not find container for link: {listing['link']}")
                 container = soup # Fallback

            # 2. Extract Price (find span with specific style nearby)
            # Note: Style matching can be fragile.
            price_tag = container.find('span', style=lambda s: s and 'font-weight: bold' in s.lower() and 'font-size: 22px' in s.lower())
            if price_tag:
                 listing['price'] = extract_number(price_tag.get_text(strip=True), is_float=True)
            else:
                  # Fallback: Search for price pattern within container if specific tag fails
                  price_match = re.search(r'([\d\.,]+)\s*€', container.get_text(" ", strip=True))
                  if price_match:
                      listing['price'] = extract_number(price_match.group(1), is_float=True)
                      logging.debug(f"[Idealista] Price found via regex fallback for {listing['link']}")
                  else:
                      logging.debug(f"[Idealista] Price tag/pattern not found for {listing['link']}")


            # 3. Extract Size (find div with m² inside)
            # Find divs that contain 'm²' and digits
            size_divs = container.find_all('div', string=re.compile(r'\d+\s*m[²2q]', re.IGNORECASE))
            size_div = None
            if size_divs:
                 size_div = size_divs[0] # Assume the first one found in the container is correct
                 sqm_match = re.search(r'(\d+)\s*m[²2q]', size_div.get_text(strip=True), re.IGNORECASE)
                 if sqm_match:
                     listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else:
                 # Fallback: Search the whole container text if specific div not found
                 sqm_match = re.search(r'(\d+)\s*m[²2q]', container.get_text(" ", strip=True), re.IGNORECASE)
                 if sqm_match:
                     listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
                     logging.debug(f"[Idealista] Size found via regex fallback for {listing['link']}")
                 else:
                      logging.debug(f"[Idealista] Size div/pattern not found for {listing['link']}")


            # Only add if we have the essentials
            if listing['link'] and listing['name']:
                 results.append(listing)
                 logging.debug(f"[Idealista] Parsed: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else:
                 logging.debug(f"[Idealista] Skipped partial data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"[Idealista] Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False)

    logging.info(f"[Idealista] Successfully parsed {len(results)} listings from this email.")
    return results


# --- Processing and Filtering (validate_and_enrich_listing, compute_scores - unchanged) ---
# Minor refinement in validation logging
def validate_and_enrich_listing(listing):
    """Validates listing based on criteria and calculates price per sqm."""
    link_for_log = listing.get('link', 'No Link')
    name_for_log = listing.get('name', 'No Name')[:60] + "..."

    if not all([listing.get('name'), listing.get('square_meters'), listing.get('price')]):
        logging.debug(f"Validation fail: Missing essential data (name/sqm/price) - {link_for_log}")
        return None
    name_lower = listing['name'].lower()
    if any(bad in name_lower for bad in BAD_KEYWORDS):
        keyword_found = next((bad for bad in BAD_KEYWORDS if bad in name_lower), '')
        logging.debug(f"Validation fail: Bad keyword '{keyword_found}' - {name_for_log}")
        return None
    sqm = listing['square_meters']
    if not isinstance(sqm, (int, float)) or not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        logging.debug(f"Validation fail: Sqm out of range ({sqm}) - {name_for_log}")
        return None
    price = listing['price']
    if not isinstance(price, (int, float)) or price <= 0 or sqm <=0 :
        logging.debug(f"Validation fail: Invalid price ({price}) or sqm ({sqm}) for calc - {name_for_log}")
        return None
    try:
        price_per_sqm = round(price / sqm, 2)
    except ZeroDivisionError:
         logging.debug(f"Validation fail: Zero Division Error for sqm ({sqm}) - {name_for_log}")
         return None
    listing['price_per_sqm'] = price_per_sqm # Store calculated value
    if price_per_sqm < MIN_PRICE_PER_SQM:
        logging.debug(f"Validation fail: Price/sqm too low ({price_per_sqm:.0f}) - {name_for_log}")
        return None
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            logging.debug(f"Validation fail: Listing too old (received {listing['received_time']}) - {name_for_log}")
            return None
    except (ValueError, TypeError) as e:
        logging.warning(f"⚠️ Could not parse received_time '{listing.get('received_time')}' for {link_for_log}: {e}")
        return None
    try:
        # Location extraction (same as before)
        parts = listing['name'].split(',')
        if len(parts) > 1:
            location_raw = parts[-1].strip()
            location_clean = re.sub(r'\(\w{2}\)$', '', location_raw).strip()
            location_clean = re.sub(r'^\d{5}\s*', '', location_clean).strip()
            listing['location'] = location_clean if location_clean else "Unknown"
        elif ' in ' in listing['name']:
            location_raw = listing['name'].split(' in ')[-1].strip()
            location_clean = re.sub(r'\(\w{2}\)$', '', location_raw).strip()
            listing['location'] = location_clean if location_clean else "Unknown"
        else: listing['location'] = "Unknown"
    except Exception as e:
        logging.warning(f"⚠️ Error extracting location from '{listing['name']}': {e}")
        listing['location'] = "Error"

    logging.debug(f"Validation OK: {name_for_log} ({listing['price_per_sqm']:.0f} €/mq)")
    return listing

def compute_scores(listings):
    """Computes a score for each listing based on price/sqm and recency."""
    # (compute_scores logic remains the same as previous version)
    if not listings: return []
    valid_listings_for_scoring = []
    for l in listings:
        try:
            if isinstance(l.get('price_per_sqm'), (int, float)) and isinstance(l.get('received_time'), str):
                datetime.fromisoformat(l['received_time']) # Check date format
                valid_listings_for_scoring.append(l)
            else: l['score'] = 0.0
        except (ValueError, TypeError): l['score'] = 0.0
    if not valid_listings_for_scoring:
        logging.warning("⚠️ No listings with valid price/sqm and received_time found for scoring.")
        for l in listings:
            if 'score' not in l: l['score'] = 0.0
        return listings
    prices = [l['price_per_sqm'] for l in valid_listings_for_scoring]
    try: times = [datetime.fromisoformat(l['received_time']).timestamp() for l in valid_listings_for_scoring]
    except ValueError as e:
        logging.error(f"❌ Error converting received_time to timestamp during scoring: {e}. Assigning 0 scores.")
        for l in listings: l['score'] = 0.0
        return listings
    min_price, max_price = min(prices), max(prices)
    price_range = max_price - min_price if max_price > min_price else 1.0
    min_time, max_time = min(times), max(times)
    time_range = max_time - min_time if max_time > min_time else 1.0
    scored_listings_map = {l['link']: l for l in valid_listings_for_scoring}
    for listing in listings:
        link = listing.get('link')
        if link in scored_listings_map:
            score_listing = scored_listings_map[link]
            try:
                price_per_sqm = score_listing['price_per_sqm']
                timestamp = datetime.fromisoformat(score_listing['received_time']).timestamp()
                norm_price = (price_per_sqm - min_price) / price_range
                inverted_norm_price = 1.0 - norm_price
                norm_time = (timestamp - min_time) / time_range
                listing['score'] = round(PRICE_WEIGHT * inverted_norm_price + RECENCY_WEIGHT * norm_time, 4)
            except Exception as e:
                 logging.warning(f"⚠️ Error calculating score for {link}: {e}")
                 listing['score'] = 0.0
        else:
             if 'score' not in listing: listing['score'] = 0.0
    listings.sort(key=lambda l: l.get('score', 0.0), reverse=True)
    logging.info(f"📊 Computed scores for {len(valid_listings_for_scoring)} listings.")
    return listings


# --- Main Execution Logic ---

def scrape_emails():
    """
    Connects, fetches ALL matching emails, parses, filters, deduplicates (within run),
    scores, and saves listings, OVERWRITING the output file.
    """
    mail = None
    all_found_listings_this_run = [] # Start fresh list for this run
    seen_links_this_run = set()      # Track links seen *only* in this run

    try:
        mail = connect_to_mail()
        mail.select('inbox')

        logging.info(f"🔍 Searching mailbox for ALL emails with query: {EMAIL_SEARCH_QUERY}")
        try:
            status, data = mail.search(None, EMAIL_SEARCH_QUERY)
            if status != 'OK':
                logging.error(f"❌ Mailbox search command failed with status: {status}")
                return
            if not data or not data[0].strip():
                logging.info("✅ No emails matching criteria found in the mailbox.")
                save_listings([]) # Save empty list to overwrite
                return
            email_ids = data[0].split()
        except Exception as e:
             logging.error(f"❌ Error during IMAP search: {e}")
             return

        logging.info(f"Found {len(email_ids)} emails matching criteria. Processing all...")

        processed_emails = 0
        total_valid_added_this_run = 0

        for eid in email_ids: # Process all found emails
            eid_str = eid.decode()
            processed_emails += 1
            logging.info(f"-- Processing email {processed_emails}/{len(email_ids)} (ID: {eid_str})...")
            email_processed_successfully = False # Flag per email

            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK':
                    logging.warning(f"⚠️ Failed to fetch email ID {eid_str}, status: {status}")
                    continue

                if not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
                    logging.warning(f"⚠️ Unexpected data format for email ID {eid_str}: {msg_data[0]}")
                    continue

                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)

                # Extract received time (same logic as before)
                received_time_str = msg['Date']
                received_dt_naive = utils.parsedate_to_datetime(received_time_str)
                if received_dt_naive:
                    if received_dt_naive.tzinfo is None or received_dt_naive.tzinfo.utcoffset(received_dt_naive) is None:
                         received_dt_aware = received_dt_naive.replace(tzinfo=timezone.utc)
                    else: received_dt_aware = received_dt_naive.astimezone(timezone.utc)
                    received_time_iso = received_dt_aware.isoformat()
                else:
                    logging.warning(f"⚠️ Could not parse date '{received_time_str}' for email ID {eid_str}. Using current time.")
                    received_time_iso = datetime.now(timezone.utc).isoformat()

                # Find HTML part (same logic as before)
                html_body = None
                if msg.is_multipart():
                    for part in msg.walk():
                         content_type = part.get_content_type()
                         content_disposition = str(part.get("Content-Disposition"))
                         if content_type == 'text/html' and 'attachment' not in content_disposition:
                             try:
                                 charset = part.get_content_charset() or 'utf-8'
                                 html_body = part.get_payload(decode=True).decode(charset, errors='replace')
                                 break
                             except Exception as e: html_body = None; logging.warning(f"⚠️ Error decoding HTML part for email ID {eid_str}: {e}")
                else:
                     if msg.get_content_type() == 'text/html':
                          try:
                              charset = msg.get_content_charset() or 'utf-8'; html_body = msg.get_payload(decode=True).decode(charset, errors='replace')
                          except Exception as e: logging.warning(f"⚠️ Error decoding non-multipart HTML for email ID {eid_str}: {e}")

                if not html_body:
                    logging.warning(f"⚠️ No suitable HTML body found in email ID {eid_str}.")
                    email_processed_successfully = True # Can mark as seen, nothing to parse
                    continue

                # Parse the HTML
                try:
                    soup = BeautifulSoup(html_body, 'html.parser')
                except Exception as e:
                    logging.error(f"❌ BeautifulSoup failed to parse HTML for email ID {eid_str}: {e}")
                    continue # Skip this email

                # --- Extract listings using ALL parsers ---
                logging.debug(f"Email ID {eid_str}: Running parsers...")
                potential_listings = []
                potential_listings.extend(parse_casait(soup, received_time_iso))
                potential_listings.extend(parse_immobiliare(soup, received_time_iso))
                potential_listings.extend(parse_idealista(soup, received_time_iso))

                logging.debug(f"Email ID {eid_str}: Extracted {len(potential_listings)} potential listings total. Validating and Deduplicating...")

                # --- Validate, enrich, check duplicates (within run), and add ---
                listings_added_from_email = 0
                for potential_listing in potential_listings:
                    link = potential_listing.get('link')

                    # 1. Check Link Duplicates (within this run)
                    if link and link in seen_links_this_run:
                        logging.debug(f"Duplicate check (Run): Link seen - {link}")
                        continue
                    if not link:
                        logging.debug(f"Skipping potential listing with no link.")
                        continue

                    # 2. Validate Listing
                    validated_listing = validate_and_enrich_listing(potential_listing)
                    if not validated_listing:
                        continue # Skip if invalid

                    # 3. Check Name Similarity Duplicates (against listings added *in this run*)
                    is_similar = False
                    for existing_listing in all_found_listings_this_run:
                        if are_names_similar(validated_listing['name'], existing_listing.get('name', '')):
                             logging.info(f"Duplicate check (Run): Similar name! '{validated_listing['name'][:50]}...' ~ '{existing_listing.get('name', '')[:50]}...'")
                             is_similar = True
                             break
                    if is_similar: continue

                    # --- If all checks pass, add the new listing TO THIS RUN'S LIST ---
                    all_found_listings_this_run.append(validated_listing)
                    seen_links_this_run.add(link)
                    total_valid_added_this_run += 1
                    listings_added_from_email += 1
                    logging.info(f"➕ Staged NEW listing ({validated_listing['source']}): {validated_listing['name'][:70]}...")

                logging.debug(f"Email ID {eid_str}: Finished checks. Staged {listings_added_from_email} new listings from this email.")
                email_processed_successfully = True # Mark email as processed

            except Exception as e:
                logging.error(f"❌❌ Unhandled error processing email ID {eid_str}: {e}", exc_info=True)

            finally: # Ensure Seen flag is handled
                 if email_processed_successfully:
                     try:
                         status, _ = mail.store(eid, '+FLAGS', '\\Seen')
                         if status == 'OK': logging.debug(f"Marked email {eid_str} as Seen.")
                         else: logging.warning(f"⚠️ Failed to mark email {eid_str} as Seen, status: {status}")
                     except Exception as e:
                         logging.warning(f"⚠️ Exception occurred while marking email {eid_str} as Seen: {e}")
                 else:
                      logging.warning(f"Email {eid_str} not marked as Seen due to processing errors.")


        # --- Final Processing for the entire run ---
        logging.info(f"-- Finished processing all {processed_emails} emails.")
        logging.info(f"-- Found {total_valid_added_this_run} unique, valid listings in this run.")

        if total_valid_added_this_run > 0:
            logging.info("Calculating scores for found listings...")
            all_listings_scored = compute_scores(all_found_listings_this_run)
            save_listings(all_listings_scored)
        else:
            logging.info("No valid listings found in this run. Saving empty file.")
            save_listings([]) # Ensure overwrite with empty list if nothing found


    except imaplib.IMAP4.error as e:
         logging.critical(f"💥 IMAP Error occurred: {e}", exc_info=True)
    except Exception as e:
         logging.critical(f"💥 Unhandled exception in main process: {e}", exc_info=True)
    finally:
        # --- Logout ---
        if mail:
            try: mail.logout(); logging.info("🚪 Logged out from email account.")
            except Exception as e: logging.warning(f"⚠️ Error during email logout: {e}")


if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process (Processing ALL emails, Overwriting output)...")
    scrape_emails()
    logging.info("🏁 Scraping process finished.")



















































