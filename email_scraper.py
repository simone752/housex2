import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re
import string # Import string for punctuation removal

# Load environment variables
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
LISTINGS_FILE = 'listings.json'
BAD_KEYWORDS = ['stazione', 'asta', 'affitto'] # Keywords indicating unwanted listings
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=30) # Max age based on email received time
MIN_PRICE_PER_SQM = 1700
SIMILARITY_WORD_SEQUENCE = 5 # Number of consecutive words to consider for similarity


def connect_mail():
    """Connects to the IMAP server and logs in."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("✅ Connected to email")
        return mail
    except imaplib.IMAP4.error as e:
        print(f"❌ IMAP Connection error: {e}")
        raise # Re-raise the exception to stop execution if connection fails


def load_listings():
    """Loads existing listings from the JSON file."""
    if not os.path.exists(LISTINGS_FILE) or os.path.getsize(LISTINGS_FILE) == 0:
        print("ℹ️ Listings file not found or empty. Starting fresh.")
        return []
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            listings = json.load(f)
            print(f"✅ Loaded {len(listings)} existing listings.")
            return listings
    except json.JSONDecodeError:
        print(f"⚠️ {LISTINGS_FILE} is invalid or corrupted. Starting fresh.")
        return []
    except Exception as e:
        print(f"❌ Error loading listings: {e}")
        return [] # Return empty list on other errors


def save_listings(listings):
    """Saves the list of listings to the JSON file."""
    try:
        with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        print(f"💾 Saved {len(listings)} listings to {LISTINGS_FILE}")
    except Exception as e:
        print(f"❌ Error saving listings: {e}")


def normalize_name(name):
    """Normalizes a listing name for comparison."""
    name = name.lower()
    # Remove punctuation more thoroughly
    name = name.translate(str.maketrans('', '', string.punctuation))
    # Split into words and remove empty strings resulting from multiple spaces
    return [word for word in name.split() if word]


def are_names_similar(name1, name2, min_sequence=SIMILARITY_WORD_SEQUENCE):
    """Checks if two names share a sequence of at least min_sequence words."""
    words1 = normalize_name(name1)
    words2 = normalize_name(name2)

    if not words1 or not words2 or len(words1) < min_sequence or len(words2) < min_sequence:
        return False # Not enough words to compare

    # Create sets of n-grams (sequences of words) for efficient comparison
    ngrams1 = set()
    for i in range(len(words1) - min_sequence + 1):
        ngrams1.add(tuple(words1[i:i + min_sequence]))

    ngrams2 = set()
    for i in range(len(words2) - min_sequence + 1):
        ngrams2.add(tuple(words2[i:i + min_sequence]))

    # Return True if there is any common sequence
    return not ngrams1.isdisjoint(ngrams2)


def extract_listings_from_email(body, received_time):
    """Extracts listing details from email HTML content."""
    soup = BeautifulSoup(body, 'html.parser')
    results = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # --- IMMOBILIARE.IT listings ---
    # Find listing blocks first if possible, then extract details relative to the block
    # This example keeps the original logic but acknowledges it might be fragile
    immo_tags = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1'))
    for tag in immo_tags:
        listing = {
            'name': ' '.join(tag.text.split()), # Normalize whitespace
            'link': tag['href'],
            'square_meters': None,
            'price': None,
            'location': 'Unknown',
            'source': 'immobiliare.it',
            'extracted_time': now_iso,
            'received_time': received_time
        }

        # Try finding details based on surrounding elements (adjust selectors if HTML changes)
        parent_td = tag.find_parent('td')
        if parent_td:
            features_td = parent_td.find_next_sibling('td', class_='realEstateBlock__features')
            if features_td:
                sqm_match = re.search(r'(\d+)\s*m[²2]', features_td.text) # Handle m² and m2
                if sqm_match:
                    listing['square_meters'] = int(sqm_match.group(1))

            price_td = parent_td.find_next_sibling('td', class_='realEstateBlock__price')
            if price_td:
                price_text = price_td.text.replace('€', '').replace('.', '').replace(',', '.').strip()
                price_match = re.search(r'^([\d\.]+)', price_text) # Match price at the start
                if price_match:
                    try:
                        listing['price'] = float(price_match.group(1))
                    except ValueError:
                        print(f"⚠️ Could not parse price: {price_td.text.strip()}")

        results.append(listing)

    # --- CASA.IT listings ---
    casa_tags = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'), style=re.compile(r'color:\s*#1A1F24'))
    for tag in casa_tags:
        listing = {
            'name': ' '.join(tag.text.split()), # Normalize whitespace
            'link': tag['href'],
            'square_meters': None,
            'price': None,
            'location': 'Unknown',
            'source': 'casa.it',
            'extracted_time': now_iso,
            'received_time': received_time
        }

        # Try finding details based on surrounding elements
        # NOTE: These selectors based on inline styles are very fragile!
        container = tag.find_parent() # Need a more specific container if possible
        if container:
             # Attempt to find size based on text pattern or sibling/nearby elements
             # This part is highly dependent on the exact email structure
             size_tag = container.find('span', text=re.compile(r'\d+\s*m[²2]')) # More direct search
             if not size_tag: # Fallback to style (less reliable)
                 size_tag = container.find('span', style=re.compile(r'padding-right:\s*10px'))

             if size_tag:
                sqm_match = re.search(r'(\d+)', size_tag.text)
                if sqm_match:
                    listing['square_meters'] = int(sqm_match.group(1))

             # Attempt to find price
             price_tag = container.find('span', style=re.compile(r'font-weight:\s*bold')) # Fragile selector
             if price_tag:
                price_text = price_tag.text.replace('€', '').replace('.', '').replace(',', '.').strip()
                price_match = re.search(r'^([\d\.]+)', price_text)
                if price_match:
                    try:
                        listing['price'] = float(price_match.group(1))
                    except ValueError:
                         print(f"⚠️ Could not parse price: {price_tag.text.strip()}")

        results.append(listing)

    print(f"    Extracted {len(results)} potential listings from email.")
    return results


def validate_listing(listing):
    """Validates a single listing based on defined criteria."""
    name_lower = listing['name'].lower()

    if any(bad in name_lower for bad in BAD_KEYWORDS):
        # print(f"    🏷️ Skipped (bad keyword): {listing['name'][:50]}...")
        return False, "Bad Keyword"
    if not listing['square_meters']:
        # print(f"    🏷️ Skipped (missing sq m): {listing['name'][:50]}...")
        return False, "Missing SqM"
    if not listing['price']:
        # print(f"    🏷️ Skipped (missing price): {listing['name'][:50]}...")
        return False, "Missing Price"

    sqm = listing['square_meters']
    price = listing['price']

    if not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS):
        # print(f"    🏷️ Skipped (size {sqm}sqm): {listing['name'][:50]}...")
        return False, f"Size {sqm}sqm"

    # Calculate price per square meter safely
    try:
        price_per_sqm = price / sqm
    except ZeroDivisionError:
        # print(f"    🏷️ Skipped (zero sq m): {listing['name'][:50]}...")
        return False, "Zero SqM"

    if price_per_sqm < MIN_PRICE_PER_SQM:
        # print(f"    🏷️ Skipped (price/sqm {price_per_sqm:.0f} €/m²): {listing['name'][:50]}...")
        return False, f"Price/SqM {price_per_sqm:.0f}"

    # Check listing age based on email received time
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
            # print(f"    🏷️ Skipped (too old, received {received_dt.date()}): {listing['name'][:50]}...")
            return False, "Too Old"
    except ValueError:
         print(f"    ⚠️ Could not parse received_time: {listing['received_time']}")
         return False, "Invalid Date" # Treat as invalid if date is wrong

    # Try to extract location (simple approach)
    name_parts = listing['name'].split(',')
    if len(name_parts) > 1:
        listing['location'] = name_parts[-1].strip()
    else:
        # Fallback: check for ' in ' pattern
        if ' in ' in name_lower:
           listing['location'] = listing['name'].split(' in ')[-1].strip()

    # print(f"    ✅ Valid listing: {listing['name'][:50]}...")
    return True, "Valid"


def compute_score(listings):
    """Computes a score for each listing based on price/sqm and age."""
    if not listings:
        return []

    valid_listings = []
    prices = []
    times = []

    # Filter out listings missing necessary data for scoring and gather data
    for l in listings:
        if l.get('price') and l.get('square_meters') and l.get('received_time'):
             try:
                 price_per_sqm = l['price'] / l['square_meters']
                 timestamp = datetime.fromisoformat(l['received_time']).timestamp()
                 prices.append(price_per_sqm)
                 times.append(timestamp)
                 valid_listings.append(l)
             except (ZeroDivisionError, ValueError, TypeError):
                 # Add listing anyway but without score or give it a default low score
                 l['score'] = 0.0
                 valid_listings.append(l) # Keep it in the list, just unscoreable

    if not prices or not times: # No scoreable listings
        print("⚠️ No listings with sufficient data to compute scores.")
        return valid_listings # Return potentially modified list

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    # Assign scores
    price_idx = 0
    time_idx = 0
    for listing in valid_listings:
         # Check if this listing was scoreable
        if listing.get('price') and listing.get('square_meters') and listing.get('received_time'):
            try:
                current_price_per_sqm = prices[price_idx]
                current_timestamp = times[time_idx]

                # Normalize: Lower price/sqm is better (closer to 1), Newer time is better (closer to 1)
                norm_price = (max_price - current_price_per_sqm) / (max_price - min_price) if max_price != min_price else 1
                norm_time = (current_timestamp - min_time) / (max_time - min_time) if max_time != min_time else 1

                # Weighted score (e.g., 60% price, 40% time)
                score = 0.6 * norm_price + 0.4 * norm_time
                listing['score'] = round(score, 4)

                price_idx += 1
                time_idx += 1
            except (IndexError, ZeroDivisionError, ValueError, TypeError):
                 listing['score'] = 0.0 # Assign default score on error

    print(f"📊 Computed scores for {len(valid_listings)} listings.")
    return sorted(valid_listings, key=lambda x: x.get('score', 0.0), reverse=True)


def scrape_listings():
    """Main function to connect, fetch emails, extract, validate, and save listings."""
    mail = None # Initialize mail to None
    try:
        mail = connect_mail()
        mail.select('inbox') # Select the inbox

        # Search for UNSEEN emails from specific senders
        # Adjust senders as needed. Using OR for multiple senders.
        search_criteria = '(UNSEEN OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")'
        # search_criteria = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com")' # Use this to test without UNSEEN

        status, data = mail.search(None, search_criteria)
        if status != 'OK':
            print(f"❌ Failed to search emails: {status}")
            return

        email_ids = data[0].split()
        print(f"📥 Found {len(email_ids)} new/unseen emails to process.")

        if not email_ids:
            print("🏁 No new emails found. Exiting.")
            return

        all_listings = load_listings()
        # Create sets for quick lookups of existing links and exact names
        existing_links = {l['link'] for l in all_listings if l.get('link')}
        existing_names = {l['name'] for l in all_listings if l.get('name')}

        newly_added_count = 0

        # Process emails from oldest unseen to newest
        for eid in email_ids:
            eid_str = eid.decode('utf-8') # Decode bytes to string
            print(f"\n📧 Processing email ID: {eid_str}")
            try:
                # Fetch the full email content (RFC822)
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK' or not msg_data or msg_data[0] is None:
                    print(f"   ❌ Failed to fetch email ID {eid_str}: Status {status}")
                    continue # Skip this email

                msg_bytes = msg_data[0][1]
                msg = email.message_from_bytes(msg_bytes, policy=policy.default)

                # Decode header safely
                subject = ""
                try:
                     subject_header = email.header.decode_header(msg['Subject'])
                     subject = ''.join([str(s, c or 'utf-8') for s, c in subject_header])
                except Exception as e:
                     print(f"   ⚠️ Error decoding subject: {e}")
                     subject = msg['Subject'] # Fallback

                sender = msg.get('From', 'Unknown Sender')
                print(f"   From: {sender} | Subject: {subject[:70]}...")

                # Get received time (more reliable than 'Date' sometimes)
                received_time_str = None
                try:
                    date_tuple = email.utils.parsedate_tz(msg['Date'])
                    if date_tuple:
                        local_dt = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
                        received_time_str = local_dt.astimezone(timezone.utc).isoformat()
                    else: # Fallback if Date header is weird
                        received_time_str = datetime.now(timezone.utc).isoformat()
                        print("   ⚠️ Could not parse 'Date' header, using current time.")
                except Exception as e:
                    received_time_str = datetime.now(timezone.utc).isoformat()
                    print(f"   ⚠️ Error parsing date '{msg['Date']}', using current time: {e}")


                # Extract HTML body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get('Content-Disposition'))
                        if content_type == 'text/html' and 'attachment' not in content_disposition:
                            try:
                                body_bytes = part.get_payload(decode=True)
                                # Detect charset or default to utf-8
                                charset = part.get_content_charset() or 'utf-8'
                                body = body_bytes.decode(charset, errors='replace')
                                break # Found the main HTML part
                            except Exception as e:
                                print(f"   ⚠️ Error decoding HTML part: {e}")
                                body = "" # Reset body on error
                else:
                    # Not multipart, try to get content directly
                     content_type = msg.get_content_type()
                     if content_type == 'text/html':
                        try:
                            body_bytes = msg.get_payload(decode=True)
                            charset = msg.get_content_charset() or 'utf-8'
                            body = body_bytes.decode(charset, errors='replace')
                        except Exception as e:
                             print(f"   ⚠️ Error decoding non-multipart HTML: {e}")
                             body = ""


                if not body:
                    print("   ⚠️ No HTML body found in email.")
                    # Mark as seen even if no body, to avoid reprocessing
                    mail.store(eid, '+FLAGS', '\\Seen')
                    print(f"   ✅ Marked email {eid_str} as Seen (no HTML body).")
                    continue # Skip to next email

                # Extract potential listings from this email's body
                extracted_listings = extract_listings_from_email(body, received_time_str)

                processed_in_email = 0
                duplicates_in_email = 0
                invalid_in_email = 0
                added_from_email = 0

                for listing in extracted_listings:
                    processed_in_email += 1
                    # --- Duplicate Checks ---
                    # 1. Check link (most reliable)
                    if listing['link'] in existing_links:
                        # print(f"    🔗 Duplicate (Link): {listing['link']}")
                        duplicates_in_email +=1
                        continue

                    # 2. Check exact name (quick check)
                    if listing['name'] in existing_names:
                        # print(f"    📛 Duplicate (Exact Name): {listing['name'][:50]}...")
                        duplicates_in_email +=1
                        continue

                    # --- Validation ---
                    is_valid, reason = validate_listing(listing)
                    if not is_valid:
                         # print(f"    🚫 Invalid ({reason}): {listing['name'][:50]}...")
                         invalid_in_email += 1
                         continue

                    # --- Similarity Check (more expensive, do last) ---
                    is_similar = False
                    for existing_listing in all_listings:
                        # Only compare if the existing one is also valid (or skip check if needed)
                        # And avoid comparing to itself if name happens to be identical
                        if listing['name'] != existing_listing['name'] and \
                           are_names_similar(listing['name'], existing_listing['name']):
                            # print(f"    👯 Duplicate (Similar Name): {listing['name'][:50]}... vs {existing_listing['name'][:50]}...")
                            is_similar = True
                            break # Found a similar one, no need to check further

                    if is_similar:
                        duplicates_in_email +=1
                        continue

                    # --- Add New Listing ---
                    print(f"    ✨ Adding NEW listing: {listing['name'][:60]}... ({listing.get('square_meters')}sqm, €{listing.get('price')})")
                    all_listings.append(listing)
                    existing_links.add(listing['link']) # Update sets immediately
                    existing_names.add(listing['name'])
                    newly_added_count += 1
                    added_from_email += 1

                print(f"   📊 Email Stats: Processed={processed_in_email}, Added={added_from_email}, Duplicates={duplicates_in_email}, Invalid={invalid_in_email}")

                # Mark the email as Seen *after* processing successfully
                status, _ = mail.store(eid, '+FLAGS', '\\Seen')
                if status == 'OK':
                     print(f"   ✅ Marked email {eid_str} as Seen.")
                else:
                     print(f"   ⚠️ Failed to mark email {eid_str} as Seen.")

            except Exception as e:
                print(f"   ❌❌❌ An unexpected error occurred processing email ID {eid_str}: {e}")
                # Optional: Decide whether to mark as seen even on error
                # mail.store(eid, '+FLAGS', '\\Seen')

        # --- Post-Processing ---
        if newly_added_count > 0:
            print(f"\n📈 Added {newly_added_count} new listings. Recomputing scores...")
            # Recompute scores for the entire updated list
            scored_listings = compute_score(all_listings)
            save_listings(scored_listings)
        else:
            print("\n🏁 No new valid listings were added.")
            # Optionally save even if no new listings, e.g., if scores changed due to aging
            # save_listings(all_listings)

        print(f"✅ Done. Total listings in file: {len(all_listings)}")

    except imaplib.IMAP4.error as e:
        print(f"❌ IMAP Error during processing: {e}")
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")
    finally:
        # Ensure logout happens
        if mail:
            try:
                mail.logout()
                print("🚪 Logged out from email.")
            except Exception as e:
                print(f"⚠️ Error during logout: {e}")


if __name__ == '__main__':
    scrape_listings()









































