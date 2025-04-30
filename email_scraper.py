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

# Deduplication Setting
SIMILARITY_WORD_SEQUENCE = 5 # <--- Set consecutive words for similarity check

# Email Search Query (Includes UNSEEN and original senders)
# Ensure the senders are correct for your use case
BASE_SEARCH_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")'
EMAIL_SEARCH_QUERY = f'(UNSEEN {BASE_SEARCH_QUERY})' # Search only UNSEEN emails matching the base query
# Use this line below if you want to re-process all emails (remove UNSEEN) for testing:
# EMAIL_SEARCH_QUERY = BASE_SEARCH_QUERY

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---

def normalize_name(name):
    """Normalizes a listing name for comparison (lowercase, no punctuation, split)."""
    if not name:
        return []
    name = name.lower()
    # Remove punctuation
    name = name.translate(str.maketrans('', '', string.punctuation))
    # Split into words and remove empty strings resulting from multiple spaces
    return [word for word in name.split() if word]

def are_names_similar(name1, name2, min_sequence=SIMILARITY_WORD_SEQUENCE):
    """Checks if two names share a sequence of at least min_sequence words."""
    words1 = normalize_name(name1)
    words2 = normalize_name(name2)

    # Basic checks
    if not words1 or not words2 or len(words1) < min_sequence or len(words2) < min_sequence:
        return False # Not enough words in one or both names to compare

    # Create sets of n-grams (sequences of words) for efficient comparison
    ngrams1 = set()
    for i in range(len(words1) - min_sequence + 1):
        ngrams1.add(tuple(words1[i:i + min_sequence]))

    ngrams2 = set()
    for i in range(len(words2) - min_sequence + 1):
        ngrams2.add(tuple(words2[i:i + min_sequence]))

    # Return True if there is any common sequence (non-empty intersection)
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
        raise # Re-raise the exception to stop the script if connection fails

def load_existing_listings(filename=LISTINGS_FILE):
    """Loads existing listings from a JSON file."""
    listings = []
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                listings = json.load(f)
            logging.info(f"✅ Loaded {len(listings)} existing listings from {filename}")
        except json.JSONDecodeError:
            logging.warning(f"⚠️ Could not decode JSON from {filename}. Starting fresh.")
        except Exception as e:
            logging.error(f"❌ Error loading {filename}: {e}")
    else:
         logging.info(f"ℹ️ Listings file {filename} not found. Starting fresh.")
    return listings

def save_listings(listings, filename=LISTINGS_FILE):
    """Saves listings to a JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 Saved {len(listings)} listings to {filename}")
    except Exception as e:
        logging.error(f"❌ Error saving listings to {filename}: {e}")


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

# --- Parsing Logic (Keep existing logic, ensure robustness) ---

def parse_immobiliare(soup, received_time):
    """Parses listings from Immobiliare.it email HTML."""
    results = []
    # Try finding listing containers first - This might be more robust than finding links directly
    # Inspect the email HTML structure carefully to find reliable container elements.
    # Example: Find table rows that seem to contain listing info.
    # This selector is hypothetical - ADJUST IT BASED ON ACTUAL EMAIL HTML
    # listing_blocks = soup.find_all('tr', class_='listing-row') # Hypothetical class

    # Using the link seems more direct based on previous code
    listing_links = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'))

    # logging.debug(f"Found {len(listing_links)} potential immobiliare.it links.") # Use debug level

    for link_tag in listing_links:
        block = link_tag.find_parent('tr') or link_tag.find_parent('div') or link_tag # Find a reasonable parent block
        if block is None: block = link_tag # Fallback to link itself if no parent found

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
            if not link_tag.get('href'):
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
                block_text_sqm = block.get_text(" ", strip=True)
                sqm_match = re.search(r'(\d+)\s*m[q²]', block_text_sqm, re.IGNORECASE)
                if sqm_match:
                    listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)

            if listing['price'] is None:
                 block_text_price = block.get_text(" ", strip=True)
                 # Look for € symbol followed by numbers
                 price_match = re.search(r'€\s*([\d\.,]+)', block_text_price)
                 if price_match:
                     listing['price'] = extract_number(price_match.group(1), is_float=True)

            # --- Add to results if essential data found ---
            if listing['link'] and listing['name']: # Require at least link and name
                results.append(listing)
            else:
                logging.debug(f"Skipping partial immobiliare.it data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"⚠️ Parsing immobiliare.it block failed: {e}. Link: {listing.get('link') or 'N/A'}") # Log part of the failing block

    # logging.info(f"Parsed {len(results)} immobiliare.it listings from this email.") # Use info level if desired
    return results


def parse_casait(soup, received_time):
    """Parses listings from Casa.it email HTML."""
    results = []
    # Try finding container elements. ADJUST SELECTOR based on actual HTML.
    # Using the link seems more direct based on previous code
    listing_links = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'))

    # logging.debug(f"Found {len(listing_links)} potential casa.it links.")

    for link_tag in listing_links:
        block = link_tag.find_parent('tr') or link_tag.find_parent('div') or link_tag # Find a reasonable parent block
        if block is None: block = link_tag # Fallback to link itself

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
            if not link_tag.get('href'):
                logging.debug("Skipping block, no valid casa.it link found.")
                continue

            listing['link'] = link_tag['href']
            # Try to get name from a specific tag within the link or the link text itself
            title_tag = link_tag.find(['h3', 'div', 'span'], recursive=False) # Look for direct child h3/div/span
            listing['name'] = clean_text(title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True))


            # --- Extract Details (Needs careful adjustment based on HTML) ---
            # Search within the entire block text for keywords and numbers. Less precise but more robust to structure changes.
            block_text = block.get_text(" ", strip=True) # Get all text within the block

            # Extract Square Meters (look for number followed by 'mq' or 'm²')
            sqm_match = re.search(r'(\d+)\s*m[q²]', block_text, re.IGNORECASE)
            if sqm_match:
                listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            # else: # Removed unreliable style-based fallback

            # Extract Price (look for € symbol followed by numbers)
            price_match = re.search(r'€\s*([\d\.,]+)', block_text)
            if price_match:
                listing['price'] = extract_number(price_match.group(1), is_float=True)
            # else: # Removed unreliable style-based fallback

            # --- Add to results if essential data found ---
            if listing['link'] and listing['name']:
                results.append(listing)
            else:
                logging.debug(f"Skipping partial casa.it data: Link={listing['link']}, Name={listing['name']}")

        except Exception as e:
            logging.warning(f"⚠️ Parsing casa.it block failed: {e}. Link: {listing.get('link') or 'N/A'}")

    # logging.info(f"Parsed {len(results)} casa.it listings from this email.")
    return results

# --- Processing and Filtering ---

def validate_and_enrich_listing(listing):
    """Validates listing based on criteria and calculates price per sqm."""
    # Basic check for essential data parsed (name, sqm, price needed for validation)
    if not all([listing.get('name'), listing.get('square_meters'), listing.get('price')]):
        logging.debug(f"Validation fail: Missing essential data (name/sqm/price) - {listing.get('link') or 'No Link'}")
        return None # Return None if invalid

    name_lower = listing['name'].lower()

    # Check for bad keywords
    if any(bad in name_lower for bad in BAD_KEYWORDS):
        logging.debug(f"Validation fail: Bad keyword '{next((bad for bad in BAD_KEYWORDS if bad in name_lower), '')}' - {listing['name']}")
        return None

    # Validate square meters
    sqm = listing['square_meters']
    if not isinstance(sqm, (int, float)) or not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        logging.debug(f"Validation fail: Sqm out of range ({sqm}) - {listing['name']}")
        return None

    # Calculate and validate price per square meter
    price = listing['price']
    if not isinstance(price, (int, float)) or price <= 0 or sqm <=0 : # Added zero checks
        logging.debug(f"Validation fail: Invalid price ({price}) or sqm ({sqm}) for calc - {listing['name']}")
        return None

    price_per_sqm = round(price / sqm, 2)
    listing['price_per_sqm'] = price_per_sqm # Store calculated value

    if price_per_sqm < MIN_PRICE_PER_SQM:
        logging.debug(f"Validation fail: Price/sqm too low ({price_per_sqm}) - {listing['name']}")
        return None

    # Validate listing age
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            logging.debug(f"Validation fail: Listing too old (received {listing['received_time']}) - {listing['name']}")
            return None
    except (ValueError, TypeError) as e:
        logging.warning(f"⚠️ Could not parse received_time '{listing.get('received_time')}' for {listing.get('link')}: {e}")
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
            listing['location'] = location_clean if location_clean else "Unknown" # Avoid empty string
        elif ' in ' in listing['name']: # Keep the 'in' check as fallback
            location_raw = listing['name'].split(' in ')[-1].strip()
            location_clean = re.sub(r'\(\w{2}\)$', '', location_raw).strip() # Clean trailing (XX) here too
            listing['location'] = location_clean if location_clean else "Unknown"
        else:
            listing['location'] = "Unknown" # Default if no pattern matches
    except Exception as e:
        logging.warning(f"⚠️ Error extracting location from '{listing['name']}': {e}")
        listing['location'] = "Error"

    # If all checks passed
    logging.debug(f"Validation OK: {listing['name']} ({listing['price_per_sqm']} €/mq)")
    return listing # Return the enriched listing dictionary if valid

def compute_scores(listings):
    """Computes a score for each listing based on price/sqm and recency."""
    if not listings:
        return []

    # Filter out listings that might miss price_per_sqm or received_time after validation/enrichment phase
    # Ensure data types are correct before proceeding
    valid_listings_for_scoring = []
    for l in listings:
        try:
            if isinstance(l.get('price_per_sqm'), (int, float)) and isinstance(l.get('received_time'), str):
                # Check if received_time is a valid ISO format string before adding
                datetime.fromisoformat(l['received_time'])
                valid_listings_for_scoring.append(l)
            else:
                 l['score'] = 0.0 # Set score to 0 if data is invalid/missing
        except (ValueError, TypeError):
             l['score'] = 0.0 # Set score to 0 if date is invalid format


    if not valid_listings_for_scoring:
        logging.warning("⚠️ No listings with valid price/sqm and received_time found for scoring.")
        # Ensure all listings have a score attribute before returning
        for l in listings:
            if 'score' not in l: l['score'] = 0.0
        return listings # Return original list with potentially zero scores

    prices = [l['price_per_sqm'] for l in valid_listings_for_scoring]
    try:
        times = [datetime.fromisoformat(l['received_time']).timestamp() for l in valid_listings_for_scoring]
    except ValueError as e:
        logging.error(f"❌ Error converting received_time to timestamp during scoring: {e}. Assigning 0 scores.")
        # Assign 0 score to all if time conversion fails for any
        for l in listings: l['score'] = 0.0
        return listings

    # Handle cases where all prices or times are identical to avoid division by zero
    min_price, max_price = min(prices), max(prices)
    price_range = max_price - min_price if max_price > min_price else 1.0 # Avoid zero range

    min_time, max_time = min(times), max(times)
    time_range = max_time - min_time if max_time > min_time else 1.0 # Avoid zero range

    # Add scores back to the original list items
    scored_listings_map = {l['link']: l for l in valid_listings_for_scoring} # Map for quick lookup

    for listing in listings:
        link = listing.get('link')
        if link in scored_listings_map:
            score_listing = scored_listings_map[link]
            try:
                price_per_sqm = score_listing['price_per_sqm']
                timestamp = datetime.fromisoformat(score_listing['received_time']).timestamp()

                # Normalize price (lower is better, so invert: 1 - normalized)
                norm_price = (price_per_sqm - min_price) / price_range
                inverted_norm_price = 1.0 - norm_price

                # Normalize time (higher timestamp is better)
                norm_time = (timestamp - min_time) / time_range

                # Calculate weighted score
                listing['score'] = round(PRICE_WEIGHT * inverted_norm_price + RECENCY_WEIGHT * norm_time, 4)
            except Exception as e:
                 logging.warning(f"⚠️ Error calculating score for {link}: {e}")
                 listing['score'] = 0.0 # Assign default score on calculation error
        else:
            # Ensure listings not in the scorable set still have a score attribute
             if 'score' not in listing: listing['score'] = 0.0


    # Sort all listings by score (descending)
    listings.sort(key=lambda l: l.get('score', 0.0), reverse=True)
    logging.info(f"📊 Computed scores for {len(valid_listings_for_scoring)} listings.")
    return listings

# --- Main Execution Logic ---

def scrape_emails():
    """Main function to connect, fetch, parse, filter, score, and save listings."""
    mail = None # Initialize mail
    try:
        mail = connect_to_mail() # Will exit if connection fails
        mail.select('inbox') # Select mailbox (add error handling if needed)

        logging.info(f"🔍 Searching mailbox with query: {EMAIL_SEARCH_QUERY}")
        try:
            status, data = mail.search(None, EMAIL_SEARCH_QUERY)
            if status != 'OK':
                logging.error(f"❌ Mailbox search command failed with status: {status}")
                return # Exit if search fails
            if not data or not data[0].strip():
                logging.info("✅ No unseen emails matching criteria found.")
                return # Exit cleanly if no emails found
            email_ids = data[0].split()
        except Exception as e:
             logging.error(f"❌ Error during IMAP search: {e}")
             return


        logging.info(f"Found {len(email_ids)} UNSEEN emails matching criteria.")

        existing_listings = load_existing_listings()
        # Use link as the primary unique identifier
        seen_links = {l.get('link') for l in existing_listings if l.get('link')}
        logging.info(f"Loaded {len(existing_listings)} existing listings ({len(seen_links)} unique links).")

        newly_added_count = 0
        processed_emails = 0

        # Process emails (consider newest first if desired by reversing list)
        for eid in email_ids:
            eid_str = eid.decode()
            processed_emails += 1
            logging.info(f"Processing email {processed_emails}/{len(email_ids)} (ID: {eid_str})...")
            email_processed_successfully = False # Flag to track if email processing step completed

            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK':
                    logging.warning(f"⚠️ Failed to fetch email ID {eid_str}, status: {status}")
                    continue # Skip to next email ID

                # Ensure msg_data[0] is a tuple and has the email content part
                if not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
                    logging.warning(f"⚠️ Unexpected data format for email ID {eid_str}: {msg_data[0]}")
                    continue

                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)

                # Extract received time robustly
                received_time_str = msg['Date']
                received_dt_naive = utils.parsedate_to_datetime(received_time_str)
                if received_dt_naive:
                    # Attempt to make it timezone-aware (assuming UTC if no timezone info)
                    if received_dt_naive.tzinfo is None or received_dt_naive.tzinfo.utcoffset(received_dt_naive) is None:
                        received_dt_aware = received_dt_naive.replace(tzinfo=timezone.utc)
                        # logging.debug(f"Assuming UTC for received time: {received_time_str}")
                    else:
                        received_dt_aware = received_dt_naive.astimezone(timezone.utc)
                    received_time_iso = received_dt_aware.isoformat()
                else:
                    logging.warning(f"⚠️ Could not parse date '{received_time_str}' for email ID {eid_str}. Using current time.")
                    # Fallback to current time might skew recency score
                    received_time_iso = datetime.now(timezone.utc).isoformat()


                # Find HTML part
                html_body = None
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        # Check if it's HTML and not an attachment
                        if content_type == 'text/html' and 'attachment' not in content_disposition:
                            try:
                                charset = part.get_content_charset() or 'utf-8' # Default to utf-8
                                html_body = part.get_payload(decode=True).decode(charset, errors='replace')
                                # logging.debug(f"Found HTML part (charset: {charset}).")
                                break
                            except Exception as e:
                                logging.warning(f"⚠️ Error decoding HTML part for email ID {eid_str}: {e}")
                                html_body = None # Ensure it's reset if decoding fails
                else: # Handle non-multipart messages
                    if msg.get_content_type() == 'text/html':
                         try:
                             charset = msg.get_content_charset() or 'utf-8'
                             html_body = msg.get_payload(decode=True).decode(charset, errors='replace')
                             # logging.debug("Found HTML in non-multipart message.")
                         except Exception as e:
                             logging.warning(f"⚠️ Error decoding non-multipart HTML for email ID {eid_str}: {e}")

                if not html_body:
                    logging.warning(f"⚠️ No suitable HTML body found in email ID {eid_str}.")
                    email_processed_successfully = True # Mark as processed (nothing to extract)
                    continue # Go to finally block to mark as seen

                # Parse the HTML
                try:
                    soup = BeautifulSoup(html_body, 'html.parser')
                except Exception as e:
                    logging.error(f"❌ BeautifulSoup failed to parse HTML for email ID {eid_str}: {e}")
                    # Decide if you want to mark as Seen even if parsing fails
                    email_processed_successfully = False # Parsing failed, don't mark as seen yet maybe?
                    continue # Skip this email

                # --- Extract listings using refined parsers ---
                potential_listings = parse_immobiliare(soup, received_time_iso) + \
                                     parse_casait(soup, received_time_iso)

                logging.debug(f"Extracted {len(potential_listings)} potential listings from email ID {eid_str}. Validating...")

                # --- Validate, enrich, check duplicates, and add new ---
                listings_added_from_email = 0
                for potential_listing in potential_listings:
                    link = potential_listing.get('link')

                    # 1. Check Link Duplicates (Fastest Check)
                    if link and link in seen_links:
                        logging.debug(f"Duplicate check: Link seen - {link}")
                        continue
                    if not link:
                        logging.debug(f"Skipping potential listing with no link.")
                        continue

                    # 2. Validate Listing
                    validated_listing = validate_and_enrich_listing(potential_listing)
                    if not validated_listing:
                        # Logging handled within validate_and_enrich_listing
                        continue # Skip if invalid

                    # 3. Check Name Similarity Duplicates (Slower Check)
                    is_similar = False
                    for existing_listing in existing_listings: # Compare against all currently known listings
                        if are_names_similar(validated_listing['name'], existing_listing.get('name', '')):
                             logging.info(f"Duplicate check: Similar name found for '{validated_listing['name'][:50]}...' matching '{existing_listing.get('name', '')[:50]}...'")
                             is_similar = True
                             break # Found a similar one, no need to check further

                    if is_similar:
                        continue # Skip if similar name found

                    # --- If all checks pass, add the new listing ---
                    existing_listings.append(validated_listing)
                    seen_links.add(link) # Add link to seen set
                    newly_added_count += 1
                    listings_added_from_email += 1
                    logging.info(f"➕ Added NEW listing ({validated_listing['source']}): {validated_listing['name'][:70]}...")

                logging.debug(f"Finished processing potential listings for email {eid_str}. Added {listings_added_from_email}.")
                email_processed_successfully = True # Mark email as processed

            except Exception as e:
                logging.error(f"❌❌ Unhandled error processing email ID {eid_str}: {e}", exc_info=True)
                # Keep email_processed_successfully as False, so it might not be marked seen

            finally:
                 # --- Mark email as Seen in IMAP ---
                 # Only mark as seen if the processing steps were successful OR if you always want to mark regardless of errors
                 if email_processed_successfully:
                     try:
                         status, _ = mail.store(eid, '+FLAGS', '\\Seen')
                         if status == 'OK':
                             logging.debug(f"Marked email {eid_str} as Seen.")
                         else:
                             logging.warning(f"⚠️ Failed to mark email {eid_str} as Seen, status: {status}")
                     except Exception as e:
                         logging.warning(f"⚠️ Exception occurred while marking email {eid_str} as Seen: {e}")
                 else:
                      logging.warning(f"Email {eid_str} not marked as Seen due to processing errors.")


        # --- Final Processing ---
        logging.info(f"Finished processing emails. Added {newly_added_count} new listings.")

        if newly_added_count > 0 or True: # Recompute scores even if no new listings added (e.g., for aging)
             logging.info("Calculating/Updating scores...")
             all_listings_scored = compute_scores(existing_listings)
             save_listings(all_listings_scored)
        else:
             logging.info("No new listings added and no scoring update requested.")


    except imaplib.IMAP4.error as e:
         logging.critical(f"💥 IMAP Error occurred: {e}", exc_info=True)
    except Exception as e:
         logging.critical(f"💥 Unhandled exception in main process: {e}", exc_info=True) # Log traceback
    finally:
        # --- Logout ---
        if mail:
            try:
                mail.logout()
                logging.info("🚪 Logged out from email account.")
            except Exception as e:
                logging.warning(f"⚠️ Error during email logout: {e}")


if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process...")
    scrape_emails()
    logging.info("🏁 Scraping process finished.")



















































