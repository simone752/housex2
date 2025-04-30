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
import logging # Use the logging module for better output control

# --- Configuration ---
# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json' # This file will be completely overwritten each run

# Filtering Criteria
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'garage', 'box']
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=30) # Age check based on email received date
MIN_PRICE_PER_SQM = 1700

# Scoring Weights (Price vs Recency)
PRICE_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4

# Deduplication Setting
SIMILARITY_WORD_SEQUENCE = 5

# Email Search Query (Searches ALL matching emails, ignoring \Seen flag)
# Ensure the senders are correct for your use case
EMAIL_SEARCH_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")'

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions (normalize_name, are_names_similar - unchanged) ---

def normalize_name(name):
    """Normalizes a listing name for comparison (lowercase, no punctuation, split)."""
    if not name:
        return []
    name = name.lower()
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

# --- Core Functions (connect_to_mail, save_listings, clean_text, extract_number - unchanged) ---

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

# NOTE: load_existing_listings is NO LONGER CALLED in the main flow

def save_listings(listings, filename=LISTINGS_FILE):
    """Saves listings to a JSON file (OVERWRITES)."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 SAVED (Overwritten) {len(listings)} listings to {filename}")
    except Exception as e:
        logging.error(f"❌ Error saving listings to {filename}: {e}")


def clean_text(text):
    """Utility to clean whitespace and standardize text."""
    return ' '.join(text.split())

def extract_number(text, is_float=True):
    """Extracts the first number (int or float) from a string."""
    if not text: return None
    text = text.replace('.', '').replace(',', '.')
    match = re.search(r'(\d[\d\.]*)', text)
    if match:
        try:
            return float(match.group(1)) if is_float else int(float(match.group(1)))
        except ValueError:
            logging.warning(f"⚠️ Could not convert '{match.group(1)}' to number from text: '{text}'")
            return None
    return None

# --- Parsing Logic (parse_immobiliare, parse_casait - unchanged but critical for extraction) ---

def parse_immobiliare(soup, received_time):
    """Parses listings from Immobiliare.it email HTML."""
    results = []
    listing_links = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'))
    for link_tag in listing_links:
        block = link_tag.find_parent('tr') or link_tag.find_parent('div') or link_tag
        if block is None: block = link_tag
        listing = {'source': 'immobiliare.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            if not link_tag.get('href'): continue
            listing['link'] = link_tag['href']
            listing['name'] = clean_text(link_tag.get_text(strip=True))
            parent_td = link_tag.find_parent('td')
            if parent_td:
                features_td = parent_td.find_next_sibling('td', class_='realEstateBlock__features')
                if features_td:
                    match = re.search(r'(\d+)\s*m[q²]', features_td.get_text(strip=True), re.IGNORECASE)
                    if match: listing['square_meters'] = extract_number(match.group(1), is_float=False)
                price_td = parent_td.find_next_sibling('td', class_='realEstateBlock__price')
                if price_td: listing['price'] = extract_number(price_td.get_text(strip=True), is_float=True)
            if listing['square_meters'] is None:
                block_text_sqm = block.get_text(" ", strip=True)
                sqm_match = re.search(r'(\d+)\s*m[q²]', block_text_sqm, re.IGNORECASE)
                if sqm_match: listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            if listing['price'] is None:
                 block_text_price = block.get_text(" ", strip=True)
                 price_match = re.search(r'€\s*([\d\.,]+)', block_text_price)
                 if price_match: listing['price'] = extract_number(price_match.group(1), is_float=True)
            if listing['link'] and listing['name']: results.append(listing)
            else: logging.debug(f"Skipping partial immobiliare.it data: Link={listing['link']}, Name={listing['name']}")
        except Exception as e:
            logging.warning(f"⚠️ Parsing immobiliare.it block failed: {e}. Link: {listing.get('link') or 'N/A'}")
    return results

def parse_casait(soup, received_time):
    """Parses listings from Casa.it email HTML."""
    results = []
    listing_links = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'))
    for link_tag in listing_links:
        block = link_tag.find_parent('tr') or link_tag.find_parent('div') or link_tag
        if block is None: block = link_tag
        listing = {'source': 'casa.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            if not link_tag.get('href'): continue
            listing['link'] = link_tag['href']
            title_tag = link_tag.find(['h3', 'div', 'span'], recursive=False)
            listing['name'] = clean_text(title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True))
            block_text = block.get_text(" ", strip=True)
            sqm_match = re.search(r'(\d+)\s*m[q²]', block_text, re.IGNORECASE)
            if sqm_match: listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            price_match = re.search(r'€\s*([\d\.,]+)', block_text)
            if price_match: listing['price'] = extract_number(price_match.group(1), is_float=True)
            if listing['link'] and listing['name']: results.append(listing)
            else: logging.debug(f"Skipping partial casa.it data: Link={listing['link']}, Name={listing['name']}")
        except Exception as e:
            logging.warning(f"⚠️ Parsing casa.it block failed: {e}. Link: {listing.get('link') or 'N/A'}")
    return results


# --- Processing and Filtering (validate_and_enrich_listing, compute_scores - unchanged) ---

def validate_and_enrich_listing(listing):
    """Validates listing based on criteria and calculates price per sqm."""
    if not all([listing.get('name'), listing.get('square_meters'), listing.get('price')]):
        logging.debug(f"Validation fail: Missing essential data (name/sqm/price) - {listing.get('link') or 'No Link'}")
        return None
    name_lower = listing['name'].lower()
    if any(bad in name_lower for bad in BAD_KEYWORDS):
        logging.debug(f"Validation fail: Bad keyword '{next((bad for bad in BAD_KEYWORDS if bad in name_lower), '')}' - {listing['name']}")
        return None
    sqm = listing['square_meters']
    if not isinstance(sqm, (int, float)) or not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        logging.debug(f"Validation fail: Sqm out of range ({sqm}) - {listing['name']}")
        return None
    price = listing['price']
    if not isinstance(price, (int, float)) or price <= 0 or sqm <=0 :
        logging.debug(f"Validation fail: Invalid price ({price}) or sqm ({sqm}) for calc - {listing['name']}")
        return None
    price_per_sqm = round(price / sqm, 2)
    listing['price_per_sqm'] = price_per_sqm
    if price_per_sqm < MIN_PRICE_PER_SQM:
        logging.debug(f"Validation fail: Price/sqm too low ({price_per_sqm}) - {listing['name']}")
        return None
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            logging.debug(f"Validation fail: Listing too old (received {listing['received_time']}) - {listing['name']}")
            return None
    except (ValueError, TypeError) as e:
        logging.warning(f"⚠️ Could not parse received_time '{listing.get('received_time')}' for {listing.get('link')}: {e}")
        return None
    try:
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
    logging.debug(f"Validation OK: {listing['name']} ({listing['price_per_sqm']} €/mq)")
    return listing

def compute_scores(listings):
    """Computes a score for each listing based on price/sqm and recency."""
    if not listings: return []
    valid_listings_for_scoring = []
    for l in listings:
        try:
            if isinstance(l.get('price_per_sqm'), (int, float)) and isinstance(l.get('received_time'), str):
                datetime.fromisoformat(l['received_time'])
                valid_listings_for_scoring.append(l)
            else: l['score'] = 0.0
        except (ValueError, TypeError): l['score'] = 0.0
    if not valid_listings_for_scoring:
        logging.warning("⚠️ No listings with valid price/sqm and received_time found for scoring.")
        for l in listings:
            if 'score' not in l: l['score'] = 0.0
        return listings
    prices = [l['price_per_sqm'] for l in valid_listings_for_scoring]
    try:
        times = [datetime.fromisoformat(l['received_time']).timestamp() for l in valid_listings_for_scoring]
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

        # Search ALL emails matching criteria (NO 'UNSEEN')
        logging.info(f"🔍 Searching mailbox for ALL emails with query: {EMAIL_SEARCH_QUERY}")
        try:
            status, data = mail.search(None, EMAIL_SEARCH_QUERY)
            if status != 'OK':
                logging.error(f"❌ Mailbox search command failed with status: {status}")
                return
            if not data or not data[0].strip():
                logging.info("✅ No emails matching criteria found in the mailbox.")
                # Save an empty list if no emails are found to ensure overwrite
                save_listings([])
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
            logging.info(f"Processing email {processed_emails}/{len(email_ids)} (ID: {eid_str})...")
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

                # Extract received time
                received_time_str = msg['Date']
                received_dt_naive = utils.parsedate_to_datetime(received_time_str)
                if received_dt_naive:
                    if received_dt_naive.tzinfo is None or received_dt_naive.tzinfo.utcoffset(received_dt_naive) is None:
                        received_dt_aware = received_dt_naive.replace(tzinfo=timezone.utc)
                    else:
                        received_dt_aware = received_dt_naive.astimezone(timezone.utc)
                    received_time_iso = received_dt_aware.isoformat()
                else:
                    logging.warning(f"⚠️ Could not parse date '{received_time_str}' for email ID {eid_str}. Using current time.")
                    received_time_iso = datetime.now(timezone.utc).isoformat()

                # Find HTML part
                html_body = None
                # (HTML extraction logic remains the same as previous version)
                if msg.is_multipart():
                    for part in msg.walk():
                         content_type = part.get_content_type()
                         content_disposition = str(part.get("Content-Disposition"))
                         if content_type == 'text/html' and 'attachment' not in content_disposition:
                             try:
                                 charset = part.get_content_charset() or 'utf-8'
                                 html_body = part.get_payload(decode=True).decode(charset, errors='replace')
                                 break
                             except Exception as e:
                                 logging.warning(f"⚠️ Error decoding HTML part for email ID {eid_str}: {e}")
                                 html_body = None
                else:
                     if msg.get_content_type() == 'text/html':
                          try:
                              charset = msg.get_content_charset() or 'utf-8'
                              html_body = msg.get_payload(decode=True).decode(charset, errors='replace')
                          except Exception as e:
                              logging.warning(f"⚠️ Error decoding non-multipart HTML for email ID {eid_str}: {e}")


                if not html_body:
                    logging.warning(f"⚠️ No suitable HTML body found in email ID {eid_str}.")
                    email_processed_successfully = True # Mark as processed (nothing to extract)
                    continue

                # Parse the HTML
                try:
                    soup = BeautifulSoup(html_body, 'html.parser')
                except Exception as e:
                    logging.error(f"❌ BeautifulSoup failed to parse HTML for email ID {eid_str}: {e}")
                    continue # Skip this email if BS fails

                # --- Extract listings ---
                potential_listings = parse_immobiliare(soup, received_time_iso) + \
                                     parse_casait(soup, received_time_iso)

                logging.debug(f"Extracted {len(potential_listings)} potential listings from email ID {eid_str}. Validating...")

                # --- Validate, enrich, check duplicates (within run), and add ---
                listings_added_from_email = 0
                for potential_listing in potential_listings:
                    link = potential_listing.get('link')

                    # 1. Check Link Duplicates (within this run)
                    if link and link in seen_links_this_run:
                        logging.debug(f"Duplicate check (This Run): Link seen - {link}")
                        continue
                    if not link:
                        logging.debug(f"Skipping potential listing with no link.")
                        continue

                    # 2. Validate Listing
                    validated_listing = validate_and_enrich_listing(potential_listing)
                    if not validated_listing:
                        continue # Skip if invalid

                    # 3. Check Name Similarity Duplicates (against listings already added *in this run*)
                    is_similar = False
                    for existing_listing in all_found_listings_this_run: # Compare against list being built
                        if are_names_similar(validated_listing['name'], existing_listing.get('name', '')):
                             logging.info(f"Duplicate check (This Run): Similar name found for '{validated_listing['name'][:50]}...' matching '{existing_listing.get('name', '')[:50]}...'")
                             is_similar = True
                             break

                    if is_similar:
                        continue # Skip if similar name found in this run

                    # --- If all checks pass, add the new listing TO THIS RUN'S LIST ---
                    all_found_listings_this_run.append(validated_listing)
                    seen_links_this_run.add(link) # Add link to this run's seen set
                    total_valid_added_this_run += 1
                    listings_added_from_email += 1
                    logging.info(f"➕ Staged NEW listing for save ({validated_listing['source']}): {validated_listing['name'][:70]}...")

                logging.debug(f"Finished processing potential listings for email {eid_str}. Staged {listings_added_from_email} for save.")
                email_processed_successfully = True # Mark email as processed

            except Exception as e:
                logging.error(f"❌❌ Unhandled error processing email ID {eid_str}: {e}", exc_info=True)
                # Keep email_processed_successfully as False

            finally:
                 # --- Mark email as Seen in IMAP (Optional but recommended) ---
                 # Still useful for external clients or manual checks
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
        logging.info(f"Finished processing all {processed_emails} emails.")
        logging.info(f"Found {total_valid_added_this_run} unique, valid listings in this run.")

        # Compute scores for the listings found *in this run*
        logging.info("Calculating scores for found listings...")
        all_listings_scored = compute_scores(all_found_listings_this_run)

        # Save the final list, OVERWRITING the file
        save_listings(all_listings_scored)

    except imaplib.IMAP4.error as e:
         logging.critical(f"💥 IMAP Error occurred: {e}", exc_info=True)
    except Exception as e:
         logging.critical(f"💥 Unhandled exception in main process: {e}", exc_info=True)
    finally:
        # --- Logout ---
        if mail:
            try:
                mail.logout()
                logging.info("🚪 Logged out from email account.")
            except Exception as e:
                logging.warning(f"⚠️ Error during email logout: {e}")


if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process (Processing ALL emails, Overwriting output)...")
    scrape_emails()
    logging.info("🏁 Scraping process finished.")



















































