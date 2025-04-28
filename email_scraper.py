import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
import re
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import logging
import locale # For date formatting if needed

# --- Performance & Dependencies ---
try:
    import lxml
    HTML_PARSER = 'lxml'
except ImportError:
    HTML_PARSER = 'html.parser'
    logging.warning("lxml not found, using 'html.parser'. Install lxml for potential speed improvements: pip install lxml")

# --- Configuration ---
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json'

# Filtering Criteria
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'garage', 'box']
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MIN_PRICE_PER_SQM = 1700 # Minimum acceptable price per square meter

# Time-based Criteria
MAX_EMAIL_AGE = timedelta(days=20) # Fetch emails no older than this
MAX_LISTING_AGE = timedelta(days=30) # Validate listing based on received date
STALE_LISTING_THRESHOLD = timedelta(days=25) # Remove listings not seen recently (should be >= MAX_EMAIL_AGE)

# Scoring Weights (Price vs Recency)
PRICE_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4

# Email Senders (Original OR clause)
EMAIL_SENDERS_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")'
# EMAIL_SENDERS_QUERY = '(SUBJECT "nuovi annunci")' # Example alternative

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Set locale for month abbreviation formatting (IMAP standard uses English)
# This might not be strictly necessary if your system locale is already English-like
try:
    locale.setlocale(locale.LC_TIME, 'en_US.UTF-8') # Or 'C' locale
except locale.Error:
    logging.warning("Could not set locale to en_US.UTF-8. IMAP date search might use system default month names.")

# --- Core Functions (connect_to_mail, save_listings remain similar) ---

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

def load_existing_listings_dict(filename=LISTINGS_FILE):
    """Loads existing listings from JSON into a dictionary keyed by link."""
    listings_dict = {}
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                listings_list = json.load(f)
                for listing in listings_list:
                    link = listing.get('link')
                    if link:
                        # Ensure essential timestamp exists for stale check later
                        if 'last_seen_utc_iso' not in listing:
                             # If loading old data, use extracted time or a very old date
                             listing['last_seen_utc_iso'] = listing.get('extracted_time', '1970-01-01T00:00:00+00:00')
                        listings_dict[link] = listing
                logging.info(f"Loaded {len(listings_dict)} existing listings from {filename}")
        except json.JSONDecodeError:
            logging.warning(f"⚠️ Could not decode JSON from {filename}. Starting fresh.")
        except Exception as e:
            logging.error(f"⚠️ Error loading or processing {filename}: {e}")
    return listings_dict

def save_listings(listings_list, filename=LISTINGS_FILE):
    """Saves a list of listings to a JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(listings_list, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 Saved {len(listings_list)} listings to {filename}")
    except Exception as e:
         logging.error(f"❌ Error saving listings to {filename}: {e}")


def clean_text(text):
    """Utility to clean whitespace and standardize text."""
    return ' '.join(text.split()) if text else ''

def extract_number(text, is_float=True):
    """Extracts the first number (int or float) from a string."""
    if not text: return None
    text = text.replace('.', '').replace(',', '.')
    match = re.search(r'(\d[\d\.]*)', text)
    if match:
        try:
            num_str = match.group(1)
            return float(num_str) if is_float else int(float(num_str))
        except ValueError:
            logging.warning(f"⚠️ Could not convert '{match.group(1)}' to number from text: '{text}'")
    return None

# --- Parsing Logic ( Largely unchanged, ensure 'source' is set) ---
# Make sure parse_immobiliare and parse_casait initialize the listing dict
# including 'source' and 'last_seen_utc_iso': None initially

def parse_immobiliare(soup, received_time):
    results = []
    # Adjust selectors based on ACTUAL email HTML structure
    listing_blocks = soup.find_all('tr') # Example: Find all table rows
    logging.debug(f"Found {len(listing_blocks)} potential immobiliare.it blocks (e.g., <tr>).")

    for block in listing_blocks:
        # Initialize with all fields, including new ones
        listing = {
            'source': 'immobiliare.it', 'name': None, 'link': None,
            'square_meters': None, 'price': None, 'price_per_sqm': None,
            'location': '', 'received_time': received_time,
            'extracted_time': None, # Will be set when first added
            'last_seen_utc_iso': None, # Will be set when seen in this run
            'score': 0.0
        }
        try:
            link_tag = block.find('a', href=re.compile(r'https://clicks\.immobiliare\.it/'))
            if not link_tag or not link_tag.get('href'): continue

            listing['link'] = link_tag['href']
            listing['name'] = clean_text(link_tag.get_text(strip=True))
            if not listing['name']: # Sometimes name is in a child tag
                 name_tag = link_tag.find(['h2', 'h3', 'strong', 'span'], recursive=False) # Look for prominent tags
                 if name_tag: listing['name'] = clean_text(name_tag.get_text(strip=True))

            if not listing['name']: # If still no name, skip
                logging.debug(f"Skipping immobiliare block, no name found for link: {listing['link']}")
                continue

            # --- Extract Details (Adapt based on HTML) ---
            block_text = block.get_text(" ", strip=True)
            # Price
            price_match = re.search(r'€\s*([\d\.,]+)', block_text)
            if price_match: listing['price'] = extract_number(price_match.group(1))
            # Sqm
            sqm_match = re.search(r'(\d+)\s*m[q²]', block_text, re.IGNORECASE)
            if sqm_match: listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)

            # --- Add if essential data found ---
            if listing['link'] and listing['name']:
                results.append(listing)
            else:
                logging.debug(f"Skipping partial immobiliare.it data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"⚠️ Parsing immobiliare.it block failed: {e}. Block: {str(block)[:200]}...")

    logging.debug(f"Parsed {len(results)} potential immobiliare.it listings from this email.")
    return results

def parse_casait(soup, received_time):
    results = []
    # Adjust selectors based on ACTUAL email HTML structure
    listing_blocks = soup.find_all(['div', 'table']) # Example: Find divs or tables
    logging.debug(f"Found {len(listing_blocks)} potential casa.it blocks.")

    for block in listing_blocks:
        # Check if it contains a casa.it link before proceeding fully
        link_tag = block.find('a', href=re.compile(r'https://www\.casa\.it/immobili/'))
        if not link_tag or not link_tag.get('href'): continue # Skip blocks without the target link

        listing = {
            'source': 'casa.it', 'name': None, 'link': None,
            'square_meters': None, 'price': None, 'price_per_sqm': None,
            'location': '', 'received_time': received_time,
            'extracted_time': None, # Will be set when first added
            'last_seen_utc_iso': None, # Will be set when seen in this run
            'score': 0.0
        }
        try:
            listing['link'] = link_tag['href']
            title_tag = link_tag.find(['h3', 'div', 'span'], class_=re.compile('title|descrizione', re.I)) # Find common title tags/classes
            listing['name'] = clean_text(title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True))

            if not listing['name']: # If still no name, skip
                logging.debug(f"Skipping casa.it block, no name found for link: {listing['link']}")
                continue

            # --- Extract Details (Adapt based on HTML) ---
            block_text = block.get_text(" ", strip=True)
             # Price
            price_match = re.search(r'€\s*([\d\.,]+)', block_text)
            if price_match: listing['price'] = extract_number(price_match.group(1))
            # Sqm
            sqm_match = re.search(r'(\d+)\s*m[q²]', block_text, re.IGNORECASE)
            if sqm_match: listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)

            # --- Add if essential data found ---
            if listing['link'] and listing['name']:
                results.append(listing)
            else:
                 logging.debug(f"Skipping partial casa.it data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"⚠️ Parsing casa.it block failed: {e}. Block: {str(block)[:200]}...")

    logging.debug(f"Parsed {len(results)} potential casa.it listings from this email.")
    return results


# --- Processing and Filtering ---

def validate_and_enrich_listing(listing, now_utc):
    """Validates listing based on criteria, calculates price/sqm, checks age."""
    if not all([listing.get('name'), listing.get('square_meters'), listing.get('price'), listing.get('received_time')]):
        logging.debug(f"Invalid: Missing essential data - {listing.get('link') or 'No Link'}")
        return None

    # Check bad keywords
    name_lower = listing['name'].lower()
    if any(bad in name_lower for bad in BAD_KEYWORDS):
        logging.debug(f"Invalid: Bad keyword - {listing['name']}")
        return None

    # Validate square meters
    sqm = listing['square_meters']
    if not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        logging.debug(f"Invalid: Sqm ({sqm}) - {listing['name']}")
        return None

    # Calculate and validate price per square meter
    price = listing['price']
    price_per_sqm = round(price / sqm, 2)
    listing['price_per_sqm'] = price_per_sqm
    if price_per_sqm < MIN_PRICE_PER_SQM:
        logging.debug(f"Invalid: Price/sqm low ({price_per_sqm}) - {listing['name']}")
        return None

    # Validate listing age based on email received date
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        # Ensure received_dt is offset-aware for comparison
        if received_dt.tzinfo is None:
             received_dt = received_dt.replace(tzinfo=timezone.utc) # Assume UTC if naive
        else:
             received_dt = received_dt.astimezone(timezone.utc)

        if now_utc - received_dt > MAX_LISTING_AGE:
            logging.debug(f"Invalid: Listing too old (received {listing['received_time']}) - {listing['name']}")
            return None
    except (ValueError, TypeError) as e:
         logging.warning(f"⚠️ Invalid received_time format: {listing.get('received_time')} for {listing.get('link')}: {e}")
         return None

    # Extract location (simple version)
    try:
        parts = listing['name'].split(',')
        if len(parts) > 1:
            location_raw = parts[-1].strip()
            location_clean = re.sub(r'\(\w{2}\)$', '', location_raw).strip()
            listing['location'] = re.sub(r'^\d{5}\s*', '', location_clean).strip() or "Unknown"
        elif ' in ' in listing['name']:
            location_raw = listing['name'].split(' in ')[-1].strip()
            listing['location'] = re.sub(r'\(\w{2}\)$', '', location_raw).strip() or "Unknown"
        else:
             listing['location'] = "Unknown"
    except Exception:
        listing['location'] = "Error"

    # If validation passes, return the enriched listing
    logging.debug(f"Validated: {listing['name']} ({listing['price_per_sqm']} €/mq)")
    return listing


def compute_scores(listings_list):
    """Computes scores for a list of listings."""
    if not listings_list: return []

    # Ensure necessary fields exist for scoring
    scorable_listings = [l for l in listings_list if l.get('price_per_sqm') and l.get('received_time')]
    if not scorable_listings:
        logging.warning("⚠️ No listings eligible for scoring.")
        # Return list with default scores
        for l in listings_list: l['score'] = l.get('score', 0.0)
        return listings_list

    prices = [l['price_per_sqm'] for l in scorable_listings]
    try:
        # Ensure timestamps are comparable (all UTC)
        times = []
        for l in scorable_listings:
             dt = datetime.fromisoformat(l['received_time'])
             if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
             else: dt = dt.astimezone(timezone.utc)
             times.append(dt.timestamp())

    except (ValueError, TypeError) as e:
        logging.error(f"❌ Error converting received_time for scoring: {e}. Assigning default scores.")
        for l in listings_list: l['score'] = l.get('score', 0.0)
        return listings_list

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    price_range = max_price - min_price
    time_range = max_time - min_time

    # Create a mapping from link to score for efficient update
    scores = {}
    for i, l in enumerate(scorable_listings):
        price_per_sqm = prices[i]
        timestamp = times[i]
        norm_price = (price_per_sqm - min_price) / price_range if price_range else 0
        norm_time = (timestamp - min_time) / time_range if time_range else 1.0
        inverted_norm_price = 1.0 - norm_price
        score = round(PRICE_WEIGHT * inverted_norm_price + RECENCY_WEIGHT * norm_time, 4)
        if l.get('link'): scores[l['link']] = score

    # Apply scores back to the original list
    for l in listings_list:
        l['score'] = scores.get(l.get('link'), l.get('score', 0.0)) # Update if link found, else keep old/default

    listings_list.sort(key=lambda l: l.get('score', 0.0), reverse=True)
    logging.info(f"📊 Computed scores for {len(scorable_listings)} listings.")
    return listings_list


# --- Main Execution Logic ---

def scrape_emails():
    """Main function: connect, fetch recent, parse, filter, score, save."""
    mail = connect_to_mail()
    mail.select('inbox')

    # --- Calculate date for IMAP search ---
    search_since_dt = datetime.now(timezone.utc) - MAX_EMAIL_AGE
    # IMAP date format: DD-Mon-YYYY (e.g., 01-Jan-2023)
    search_since_str = search_since_dt.strftime("%d-%b-%Y")

    # --- Construct search query ---
    # Combine date criteria AND sender criteria
    # Ensure sender query is enclosed in parentheses if it uses OR
    final_search_query = f'(AND (SINCE {search_since_str}) {EMAIL_SENDERS_QUERY})'
    logging.info(f"🔍 Searching mailbox with query: {final_search_query}")

    # --- Search Mailbox ---
    try:
        status, data = mail.search(None, final_search_query)
        if status != 'OK':
            logging.error(f"❌ Mailbox search failed. Status: {status}, Response: {data}")
            mail.logout()
            return
        if not data or not data[0].strip():
            logging.info("📬 No new emails found matching criteria since {search_since_str}.")
            mail.logout()
            # Still proceed to filter/save existing listings for staleness
            # return # Or exit if no new emails means no updates needed? Decide based on workflow
        else:
             email_ids = data[0].split()
             logging.info(f"Found {len(email_ids)} emails since {search_since_str} matching sender criteria.")
    except Exception as e:
        logging.error(f"❌ Error during IMAP search: {e}")
        mail.logout()
        return


    # --- Load existing listings into a dictionary for fast lookup ---
    existing_listings_dict = load_existing_listings_dict()
    processed_links_this_run = set() # Track links updated in this run

    # --- Process Emails ---
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    processed_emails = 0

    if 'email_ids' in locals() and email_ids: # Check if email_ids exist
        for eid in reversed(email_ids): # Process newest first
            processed_emails += 1
            logging.info(f"Processing email {processed_emails}/{len(email_ids)} (ID: {eid.decode()})...")
            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK' or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
                    logging.warning(f"⚠️ Failed to fetch or invalid data for email ID {eid.decode()}")
                    continue

                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
                received_time_str = msg['Date']
                received_dt_naive = utils.parsedate_to_datetime(received_time_str)

                if received_dt_naive:
                    if received_dt_naive.tzinfo is None: received_dt_aware = received_dt_naive.replace(tzinfo=timezone.utc)
                    else: received_dt_aware = received_dt_naive.astimezone(timezone.utc)
                    received_time_iso = received_dt_aware.isoformat()
                else:
                    logging.warning(f"⚠️ No valid date ({received_time_str}) for email ID {eid.decode()}. Skipping.")
                    continue

                # Find HTML part (robustly)
                html_body = None
                # ... [HTML extraction logic from previous version - ensure it's robust] ...
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
                                logging.warning(f"⚠️ Error decoding HTML part: {e}")
                                html_body = None
                elif msg.get_content_type() == 'text/html':
                    try:
                        charset = msg.get_content_charset() or 'utf-8'
                        html_body = msg.get_payload(decode=True).decode(charset, errors='replace')
                    except Exception as e:
                        logging.warning(f"⚠️ Error decoding non-multipart HTML: {e}")

                if not html_body:
                    logging.warning(f"⚠️ No HTML body found in email ID {eid.decode()}.")
                    continue

                soup = BeautifulSoup(html_body, HTML_PARSER)

                # Parse potential listings
                potential_listings = parse_immobiliare(soup, received_time_iso) + \
                                     parse_casait(soup, received_time_iso)

                logging.info(f"Extracted {len(potential_listings)} potential items from email ID {eid.decode()}.")

                # Process each potential listing from this email
                for potential in potential_listings:
                    link = potential.get('link')
                    if not link:
                         logging.debug("Skipping item with no link.")
                         continue

                    # Mark this link as seen in this run
                    processed_links_this_run.add(link)

                    # Set the last_seen time for this item from this run
                    potential['last_seen_utc_iso'] = now_iso

                    if link in existing_listings_dict:
                        # --- Update Existing Listing ---
                        logging.debug(f"Updating existing listing: {link}")
                        # Preserve original extracted_time and score, update others if changed
                        existing = existing_listings_dict[link]
                        existing['received_time'] = potential['received_time'] # Update if email is newer
                        existing['last_seen_utc_iso'] = now_iso # Mark as seen now!
                        # Re-validate and enrich with potentially new data (price, sqm might change)
                        validated_update = validate_and_enrich_listing(potential, now_utc)
                        if validated_update:
                             # Only update fields that might change, keep original score until recalculation
                             existing['name'] = validated_update['name']
                             existing['square_meters'] = validated_update['square_meters']
                             existing['price'] = validated_update['price']
                             existing['price_per_sqm'] = validated_update['price_per_sqm']
                             existing['location'] = validated_update['location']
                        else:
                            # If it becomes invalid now, mark it seen but it might get filtered later
                            logging.warning(f"Existing listing {link} no longer validates. Marked as seen, may be filtered.")
                            # Keep old data but update last_seen

                    else:
                        # --- Process New Listing ---
                        validated_new = validate_and_enrich_listing(potential, now_utc)
                        if validated_new:
                            validated_new['extracted_time'] = now_iso # Set initial extraction time
                            validated_new['last_seen_utc_iso'] = now_iso # Also set last seen
                            existing_listings_dict[link] = validated_new # Add to our dictionary
                            logging.info(f"➕ Added new valid listing: {validated_new['name']}")

            except Exception as e:
                logging.error(f"❌ Unexpected error processing email ID {eid.decode()}: {e}", exc_info=True)

    # --- End Email Processing Loop ---
    logging.info(f"Finished processing {processed_emails} emails.")
    mail.logout()
    logging.info("🚪 Logged out from email account.")


    # --- Filter Stale Listings ---
    logging.info(f"Filtering stale listings (older than {STALE_LISTING_THRESHOLD})...")
    active_listings_list = []
    stale_count = 0
    initial_count = len(existing_listings_dict)

    for link, listing in existing_listings_dict.items():
        try:
            last_seen_dt = datetime.fromisoformat(listing['last_seen_utc_iso'])
            # Ensure it's timezone-aware (should be if saved correctly)
            if last_seen_dt.tzinfo is None: last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)

            if now_utc - last_seen_dt < STALE_LISTING_THRESHOLD:
                active_listings_list.append(listing)
            else:
                logging.info(f"➖ Removing stale listing (last seen {listing['last_seen_utc_iso']}): {listing.get('name', link)}")
                stale_count += 1
        except (ValueError, TypeError, KeyError) as e:
            logging.warning(f"⚠️ Error checking staleness for {link}, keeping it: {e}. Data: {listing.get('last_seen_utc_iso')}")
            # Keep listing if unsure about its timestamp
            active_listings_list.append(listing)

    logging.info(f"Removed {stale_count} stale listings. Kept {len(active_listings_list)} active listings from {initial_count} total.")

    # --- Final Scoring and Saving ---
    final_listings_list = compute_scores(active_listings_list)
    save_listings(final_listings_list)

    logging.info("🏁 Scraping process finished.")


if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process...")
    try:
        scrape_emails()
    except Exception as e:
        logging.critical(f"💥 Unhandled exception in main process: {e}", exc_info=True)
    logging.info("🏁 Scraping process finished (or crashed).")





