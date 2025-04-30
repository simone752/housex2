import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
import re
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import logging # Use the logging module for better output control

# --- Configuration ---
# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json'

# Filtering Criteria
BAD_KEYWORDS = ['stazione', 'asta', 'affitto', 'garage', 'box'] # Added garage/box
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=30) # Consider if older listings are still relevant
MIN_PRICE_PER_SQM = 1700 # Minimum acceptable price per square meter

# Scoring Weights (Price vs Recency) - Adjust as needed
PRICE_WEIGHT = 0.6 # How much lower price matters (0 to 1)
RECENCY_WEIGHT = 0.4 # How much newer listing matters (0 to 1)

# Email Search Query (Consider making this broader if needed)
# Alternative: Search by subject keywords like 'nuovi annunci' if sender varies
EMAIL_SEARCH_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")'
# EMAIL_SEARCH_QUERY = '(SUBJECT "nuovi annunci")' # Example alternative

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Core Functions ---

def connect_to_mail():
    """Connects to the IMAP server."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        logging.info("✅ Connected to email account")
        return mail
    except Exception as e:
        logging.error(f"❌ Error connecting to email: {e}")
        raise # Re-raise the exception to stop the script if connection fails

def load_existing_listings(filename=LISTINGS_FILE):
    """Loads existing listings from a JSON file."""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.warning(f"⚠️ Could not decode JSON from {filename}. Starting fresh.")
                return []
    return []

def save_listings(listings, filename=LISTINGS_FILE):
    """Saves listings to a JSON file."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(listings, f, indent=2, ensure_ascii=False)
    logging.info(f"💾 Saved {len(listings)} listings to {filename}")

def clean_text(text):
    """Utility to clean whitespace and standardize text."""
    return ' '.join(text.split())

def extract_number(text, is_float=True):
    """Extracts the first number (int or float) from a string."""
    if not text:
        return None
    # Remove thousands separators (dots), replace comma decimal separator with dot
    text = text.replace('.', '').replace(',', '.')
    # Find the first sequence of digits, possibly with a decimal point
    match = re.search(r'(\d[\d\.]*)', text)
    if match:
        try:
            if is_float:
                return float(match.group(1))
            else:
                # For integers like square meters, handle potential floats from bad parsing
                return int(float(match.group(1)))
        except ValueError:
            logging.warning(f"⚠️ Could not convert '{match.group(1)}' to number from text: '{text}'")
            return None
    return None

# --- Parsing Logic (Key Area for Improvement) ---

def parse_immobiliare(soup, received_time):
    """Parses listings from Immobiliare.it email HTML."""
    results = []
    # Try finding listing containers first - This might be more robust than finding links directly
    # Inspect the email HTML structure carefully to find reliable container elements.
    # Example: Find table rows that seem to contain listing info.
    # This selector is hypothetical - ADJUST IT BASED ON ACTUAL EMAIL HTML
    listing_blocks = soup.find_all('tr', class_='listing-row') # Hypothetical class

    if not listing_blocks:
        # Fallback to original link-based method if the above fails
        listing_blocks = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'))

    logging.info(f"Found {len(listing_blocks)} potential immobiliare.it blocks/links.")

    for block in listing_blocks:
        listing = {
            'source': 'immobiliare.it',
            'name': None,
            'link': None,
            'square_meters': None,
            'price': None,
            'price_per_sqm': None, # Add this field
            'location': '',
            'received_time': received_time,
            'extracted_time': datetime.now(timezone.utc).isoformat(),
            'score': 0.0 # Initialize score
        }
        try:
            # Find the primary link within the block
            link_tag = block if block.name == 'a' else block.find('a', href=re.compile(r'https://clicks\.immobiliare\.it/'))
            if not link_tag or not link_tag.get('href'):
                 logging.debug("Skipping block, no valid immobiliare.it link found.")
                 continue # Skip if no valid link found

            listing['link'] = link_tag['href']
            listing['name'] = clean_text(link_tag.get_text(strip=True))

            # --- Extract Details (Needs careful adjustment based on HTML) ---
            # This part is fragile. Inspect the HTML around the link tag.
            # Use relative positioning (find_parent, find_next_sibling) or more specific selectors.

            # Example 1: Assuming price and sqm are in sibling TDs of the link's parent TD
            parent_td = link_tag.find_parent('td')
            if parent_td:
                features_td = parent_td.find_next_sibling('td', class_='realEstateBlock__features') # Old class, might change
                if features_td:
                    # More robust regex for sqm
                    match = re.search(r'(\d+)\s*m[q²]', features_td.get_text(strip=True), re.IGNORECASE)
                    if match:
                        listing['square_meters'] = extract_number(match.group(1), is_float=False)

                price_td = parent_td.find_next_sibling('td', class_='realEstateBlock__price') # Old class, might change
                if price_td:
                    listing['price'] = extract_number(price_td.get_text(strip=True), is_float=True)

            # Example 2: Fallback - search within the entire block if the above fails
            if listing['square_meters'] is None:
                 sqm_match = re.search(r'(\d+)\s*m[q²]', block.get_text(strip=True), re.IGNORECASE)
                 if sqm_match:
                     listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)

            if listing['price'] is None:
                 # Look for € symbol followed by numbers
                 price_match = re.search(r'€\s*([\d\.,]+)', block.get_text(strip=True))
                 if price_match:
                     listing['price'] = extract_number(price_match.group(1), is_float=True)

            # --- Add to results if essential data found ---
            if listing['link'] and listing['name']: # Require at least link and name
                results.append(listing)
            else:
                logging.debug(f"Skipping partial immobiliare.it data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"⚠️ Parsing immobiliare.it block failed: {e}. Block content: {str(block)[:200]}...") # Log part of the failing block

    logging.info(f"Successfully parsed {len(results)} immobiliare.it listings from this email.")
    return results


def parse_casait(soup, received_time):
    """Parses listings from Casa.it email HTML."""
    results = []
    # Try finding container elements. ADJUST SELECTOR based on actual HTML.
    # Example: Look for divs that seem to wrap each listing.
    listing_blocks = soup.find_all('div', class_='listing-wrapper') # Hypothetical class

    if not listing_blocks:
        # Fallback to original link-based method
        listing_blocks = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'))

    logging.info(f"Found {len(listing_blocks)} potential casa.it blocks/links.")

    for block in listing_blocks:
        listing = {
            'source': 'casa.it',
            'name': None,
            'link': None,
            'square_meters': None,
            'price': None,
            'price_per_sqm': None, # Add this field
            'location': '',
            'received_time': received_time,
            'extracted_time': datetime.now(timezone.utc).isoformat(),
            'score': 0.0 # Initialize score
        }
        try:
            # Find the primary link
            link_tag = block if block.name == 'a' else block.find('a', href=re.compile(r'https://www\.casa\.it/immobili/'))
            if not link_tag or not link_tag.get('href'):
                logging.debug("Skipping block, no valid casa.it link found.")
                continue

            listing['link'] = link_tag['href']
            # Try to get name from a specific tag within the link or the link text itself
            title_tag = link_tag.find('h3') # Example: Check if title is in an H3 inside the link
            listing['name'] = clean_text(title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True))


            # --- Extract Details (Needs careful adjustment based on HTML) ---
            # Search within the entire block text for keywords and numbers. Less precise but more robust to structure changes.

            block_text = block.get_text(" ", strip=True) # Get all text within the block

            # Extract Square Meters (look for number followed by 'mq' or 'm²')
            sqm_match = re.search(r'(\d+)\s*m[q²]', block_text, re.IGNORECASE)
            if sqm_match:
                listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else:
                # Fallback: Look for specific styled spans if primary fails (less reliable)
                size_tag = block.find('span', style=re.compile(r'padding-right:\s*10px')) # Old style, might change
                if size_tag:
                     listing['square_meters'] = extract_number(size_tag.get_text(strip=True), is_float=False)


            # Extract Price (look for € symbol followed by numbers)
            price_match = re.search(r'€\s*([\d\.,]+)', block_text)
            if price_match:
                 listing['price'] = extract_number(price_match.group(1), is_float=True)
            else:
                # Fallback: Look for specific styled spans (less reliable)
                price_tag = block.find('span', style=re.compile(r'font-weight:\s*bold')) # Old style, might change
                if price_tag:
                    listing['price'] = extract_number(price_tag.get_text(strip=True), is_float=True)


            # --- Add to results if essential data found ---
            if listing['link'] and listing['name']:
                results.append(listing)
            else:
                logging.debug(f"Skipping partial casa.it data: Link={listing['link']}, Name={listing['name']}")


        except Exception as e:
            logging.warning(f"⚠️ Parsing casa.it block failed: {e}. Block content: {str(block)[:200]}...")

    logging.info(f"Successfully parsed {len(results)} casa.it listings from this email.")
    return results

# --- Processing and Filtering ---

def validate_and_enrich_listing(listing):
    """Validates listing based on criteria and calculates price per sqm."""
    # Basic check for essential data parsed
    if not all([listing.get('name'), listing.get('square_meters'), listing.get('price'), listing.get('received_time')]):
        logging.debug(f"Invalid: Missing essential data - {listing.get('link') or 'No Link'}")
        return None # Return None if invalid

    name_lower = listing['name'].lower()

    # Check for bad keywords
    if any(bad in name_lower for bad in BAD_KEYWORDS):
        logging.debug(f"Invalid: Contains bad keyword - {listing['name']}")
        return None

    # Validate square meters
    sqm = listing['square_meters']
    if not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        logging.debug(f"Invalid: Sqm out of range ({sqm}) - {listing['name']}")
        return None

    # Calculate and validate price per square meter
    price = listing['price']
    price_per_sqm = round(price / sqm, 2)
    listing['price_per_sqm'] = price_per_sqm # Store calculated value

    if price_per_sqm < MIN_PRICE_PER_SQM:
        logging.debug(f"Invalid: Price per sqm too low ({price_per_sqm}) - {listing['name']}")
        return None

    # Validate listing age
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            logging.debug(f"Invalid: Listing too old ({listing['received_time']}) - {listing['name']}")
            return None
    except ValueError:
         logging.warning(f"⚠️ Could not parse received_time: {listing.get('received_time')} for {listing.get('link')}")
         return None # Treat unparseable date as invalid

    # Attempt to extract location (Improved slightly)
    try:
        parts = listing['name'].split(',')
        if len(parts) > 1:
            # Take the last part, attempt to remove leading/trailing specifics like postal codes or province abbreviations
            location_raw = parts[-1].strip()
            # Remove trailing (XX) style province codes or leading ZIP codes
            location_clean = re.sub(r'\(\w{2}\)$', '', location_raw).strip()
            location_clean = re.sub(r'^\d{5}\s*', '', location_clean).strip()
            listing['location'] = location_clean
        elif ' in ' in listing['name']: # Keep the 'in' check as fallback
            location_raw = listing['name'].split(' in ')[-1].strip()
            listing['location'] = re.sub(r'\(\w{2}\)$', '', location_raw).strip() # Clean trailing (XX) here too
        else:
             listing['location'] = "Unknown" # Default if no pattern matches
    except Exception as e:
        logging.warning(f"⚠️ Error extracting location from '{listing['name']}': {e}")
        listing['location'] = "Error"

    logging.debug(f"Valid listing: {listing['name']} ({listing['price_per_sqm']} €/mq)")
    return listing # Return the enriched listing dictionary if valid

def compute_scores(listings):
    """Computes a score for each listing based on price/sqm and recency."""
    if not listings:
        return []

    # Filter out listings that might miss price_per_sqm or received_time after validation/enrichment phase
    valid_listings = [l for l in listings if l.get('price_per_sqm') and l.get('received_time')]
    if not valid_listings:
         logging.warning("⚠️ No listings with valid price/sqm and received_time found for scoring.")
         return listings # Return original list if none are scorable

    prices = [l['price_per_sqm'] for l in valid_listings]
    try:
        times = [datetime.fromisoformat(l['received_time']).timestamp() for l in valid_listings]
    except ValueError as e:
        logging.error(f"❌ Error converting received_time to timestamp during scoring: {e}. Cannot compute scores.")
        return listings # Return original list if time conversion fails

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    # Add scores to the original list items by matching link or another unique ID
    # This is safer if valid_listings is a subset
    for listing in listings:
         if listing in valid_listings:
            idx = valid_listings.index(listing) # Find corresponding index
            price_per_sqm = prices[idx]
            timestamp = times[idx]

            # Normalize price (lower is better, so invert: 1 - normalized)
            # Handle division by zero if all prices/times are identical
            norm_price = (price_per_sqm - min_price) / (max_price - min_price) if max_price > min_price else 0
            inverted_norm_price = 1.0 - norm_price

            # Normalize time (higher timestamp is better)
            norm_time = (timestamp - min_time) / (max_time - min_time) if max_time > min_time else 1.0 # Newest gets 1

            # Calculate weighted score
            listing['score'] = round(PRICE_WEIGHT * inverted_norm_price + RECENCY_WEIGHT * norm_time, 4)
         else:
            # Assign default score or handle listings that couldn't be scored
            listing['score'] = 0.0

    # Sort all listings by score (descending)
    listings.sort(key=lambda l: l.get('score', 0.0), reverse=True)
    logging.info(f"📊 Computed scores for {len(valid_listings)} listings.")
    return listings

# --- Main Execution Logic ---

def scrape_emails():
    """Main function to connect, fetch, parse, filter, score, and save listings."""
    mail = connect_to_mail() # Will exit if connection fails
    mail.select('inbox') # Select mailbox (add error handling if needed)

    logging.info(f"🔍 Searching mailbox with query: {EMAIL_SEARCH_QUERY}")
    status, data = mail.search(None, EMAIL_SEARCH_QUERY)

    if status != 'OK' or not data or not data[0].strip():
        logging.warning(f"⚠️ Mailbox search failed or returned no results. Status: {status}, Data: {data}")
        mail.logout()
        return

    email_ids = data[0].split()
    logging.info(f"Found {len(email_ids)} emails matching criteria.")

    existing_listings = load_existing_listings()
    # Use link as the primary unique identifier
    seen_links = {l.get('link') for l in existing_listings if l.get('link')}
    logging.info(f"Loaded {len(existing_listings)} existing listings ({len(seen_links)} unique links).")

    newly_added_count = 0
    processed_emails = 0

    # Process emails (consider newest first)
    for eid in reversed(email_ids):
        processed_emails += 1
        logging.info(f"Processing email {processed_emails}/{len(email_ids)} (ID: {eid.decode()})...")
        status, msg_data = mail.fetch(eid, '(RFC822)')

        if status != 'OK':
            logging.warning(f"⚠️ Failed to fetch email ID {eid.decode()}")
            continue

        # Ensure msg_data[0] is a tuple and has the email content part
        if not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
             logging.warning(f"⚠️ Unexpected data format for email ID {eid.decode()}: {msg_data[0]}")
             continue

        msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)

        # Extract received time robustly
        received_time_str = msg['Date']
        received_dt_naive = utils.parsedate_to_datetime(received_time_str)
        if received_dt_naive:
            # Attempt to make it timezone-aware (assuming UTC if no timezone info)
            if received_dt_naive.tzinfo is None or received_dt_naive.tzinfo.utcoffset(received_dt_naive) is None:
                 received_dt_aware = received_dt_naive.replace(tzinfo=timezone.utc)
                 logging.debug(f"Assuming UTC for received time: {received_time_str}")
            else:
                 received_dt_aware = received_dt_naive.astimezone(timezone.utc)
            received_time_iso = received_dt_aware.isoformat()
        else:
            logging.warning(f"⚠️ Could not parse date '{received_time_str}' for email ID {eid.decode()}. Skipping email.")
            continue

        # Find HTML part
        html_body = None
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                # Check if it's HTML and not an attachment
                if content_type == 'text/html' and 'attachment' not in content_disposition:
                    try:
                        # Decode payload correctly
                        charset = part.get_content_charset() or 'utf-8' # Default to utf-8
                        html_body = part.get_payload(decode=True).decode(charset, errors='replace')
                        logging.debug(f"Found HTML part (charset: {charset}).")
                        break
                    except Exception as e:
                        logging.warning(f"⚠️ Error decoding HTML part for email ID {eid.decode()}: {e}")
                        html_body = None # Ensure it's reset if decoding fails
        else:
            # Handle non-multipart messages (less common for HTML emails)
            if msg.get_content_type() == 'text/html':
                 try:
                     charset = msg.get_content_charset() or 'utf-8'
                     html_body = msg.get_payload(decode=True).decode(charset, errors='replace')
                     logging.debug("Found HTML in non-multipart message.")
                 except Exception as e:
                     logging.warning(f"⚠️ Error decoding non-multipart HTML for email ID {eid.decode()}: {e}")


        if not html_body:
            logging.warning(f"⚠️ No suitable HTML body found in email ID {eid.decode()}. Skipping.")
            continue

        # Parse the HTML
        try:
            soup = BeautifulSoup(html_body, 'html.parser')
        except Exception as e:
            logging.error(f"❌ BeautifulSoup failed to parse HTML for email ID {eid.decode()}: {e}")
            continue # Skip email if BS fails

        # --- Extract listings using refined parsers ---
        potential_listings = parse_immobiliare(soup, received_time_iso) + \
                             parse_casait(soup, received_time_iso)

        logging.info(f"Extracted {len(potential_listings)} potential listings from email ID {eid.decode()}. Validating...")

        # --- Validate, enrich, and add new unique listings ---
        for potential_listing in potential_listings:
            link = potential_listing.get('link')
            if link and link not in seen_links:
                validated_listing = validate_and_enrich_listing(potential_listing)
                if validated_listing:
                    existing_listings.append(validated_listing)
                    seen_links.add(link) # Add link to seen set
                    newly_added_count += 1
                    logging.info(f"➕ Added new valid listing: {validated_listing['name']}")
                else:
                     # Logging handled within validate_and_enrich_listing
                     pass
            elif link in seen_links:
                 logging.debug(f"Skipping duplicate listing (link already seen): {link}")
            elif not link:
                 logging.debug(f"Skipping potential listing with no link.")

    # --- Final Processing ---
    logging.info(f"Finished processing emails. Added {newly_added_count} new listings.")

    # Compute scores for the entire updated list
    all_listings_scored = compute_scores(existing_listings)

    # Save the final list
    save_listings(all_listings_scored)

    # Logout
    try:
        mail.logout()
        logging.info("🚪 Logged out from email account.")
    except Exception as e:
        logging.warning(f"⚠️ Error during email logout: {e}")


if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process...")
    try:
        scrape_emails()
    except Exception as e:
        logging.critical(f"💥 Unhandled exception in main process: {e}", exc_info=True) # Log traceback
    logging.info("🏁 Scraping process finished.")



















































