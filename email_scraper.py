# -*- coding: utf-8 -*-
import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
import re
import string
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import logging

# --- Configuration ---
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

LISTINGS_FILE = 'listings.json' # OVERWRITTEN EACH RUN

# Filtering Criteria (Using combined list from examples)
BAD_KEYWORDS = ['asta', 'affitto', 'garage', 'box', 'ufficio', 'laboratorio', 'negozio', 'capannone', 'stazione', 'corsica', 'mansarda', 'villaggio']
MAX_SQUARE_METERS = 105
MIN_SQUARE_METERS = 60
MAX_LISTING_AGE = timedelta(days=45) # Allow slightly older when rescanning all
MIN_PRICE_PER_SQM = 1700

# Scoring Weights
PRICE_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4

# Deduplication Setting
SIMILARITY_WORD_SEQUENCE = 5

# Email Search Query (Searches ALL matching emails)
# Ensure senders are correct!
EMAIL_SEARCH_QUERY = '(OR FROM "noreply@notifiche.immobiliare.it" FROM "noreply_at_casa.it_4j78rss9@duck.com" FROM "alerts@idealista.com")'

# Logging Setup - Keep DEBUG for now
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
)
logging.getLogger("chardet.charsetprober").setLevel(logging.INFO)

# --- Helper Functions (Unchanged: normalize_name, are_names_similar, connect_to_mail, save_listings) ---

def normalize_name(name):
    if not name: return []
    name = name.lower().replace('\ufeff', '').translate(str.maketrans('', '', string.punctuation))
    return [word for word in name.split() if word]

def are_names_similar(name1, name2, min_sequence=SIMILARITY_WORD_SEQUENCE):
    words1 = normalize_name(name1); words2 = normalize_name(name2)
    if not words1 or not words2 or len(words1) < min_sequence or len(words2) < min_sequence: return False
    ngrams1 = {tuple(words1[i:i + min_sequence]) for i in range(len(words1) - min_sequence + 1)}
    ngrams2 = {tuple(words2[i:i + min_sequence]) for i in range(len(words2) - min_sequence + 1)}
    return not ngrams1.isdisjoint(ngrams2)

def connect_to_mail():
    logging.debug(f"Attempting connection to {IMAP_SERVER} for user {EMAIL_ACCOUNT}...")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        logging.debug("SSL connection established. Logging in...")
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        logging.info("✅ Connected to email account")
        return mail
    except imaplib.IMAP4.error as e: logging.error(f"❌ IMAP Error during connection/login: {e}"); raise
    except Exception as e: logging.error(f"❌ Non-IMAP Error connecting to email: {e}"); raise

def save_listings(listings, filename=LISTINGS_FILE):
    logging.debug(f"Attempting to save {len(listings)} listings to {filename}")
    try:
        with open(filename, 'w', encoding='utf-8') as f: json.dump(listings, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 SAVED (Overwritten) {len(listings)} listings to {filename}")
    except Exception as e: logging.error(f"❌ Error saving listings to {filename}: {e}")

def clean_text(text):
    if not text: return ""
    text = text.replace('\ufeff', '').replace('\xa0', ' ')
    return ' '.join(text.split())

def extract_number(text, is_float=True):
    """Extracts number, handling Italian format and prefixes like 'Da '."""
    if not text: return None
    logging.debug(f"Extracting number from: '{text}'")
    text = re.sub(r'^[^\d€]*', '', text).strip() # Remove leading non-digits/€ (handles 'Da ')
    text = text.replace('€', '').strip()
    dot_count = text.count('.')
    if dot_count > 1: text = text.replace('.', '') # Assume thousands separator
    text = text.replace(',', '.') # Decimal separator

    match = re.search(r'(\d[\d\.]*)', text)
    if match:
        num_str = match.group(1)
        try:
            if num_str.endswith('.'): num_str = num_str[:-1]
            val = float(num_str)
            result = val if is_float else int(val)
            logging.debug(f"Extracted number: {result}")
            return result
        except ValueError: logging.debug(f"⚠️ Could not convert '{num_str}' to number."); return None
    logging.debug("No numeric match found.")
    return None

# --- Parsers using selectors from "Working" code + Idealista ---

def parse_immobiliare(soup, received_time):
    """Parses Immobiliare.it using selectors from the simple working example."""
    results = []
    source_id = "[Immobiliare.it - Simple Parse]"
    # Selector from simple code
    link_tags = soup.find_all('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1', re.IGNORECASE))
    logging.debug(f"{source_id} Found {len(link_tags)} potential <a> tags.")

    for i, tag in enumerate(link_tags):
        logging.debug(f"{source_id} Processing link tag {i+1}/{len(link_tags)}")
        listing = {'source': 'immobiliare.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            listing['link'] = tag.get('href')
            listing['name'] = clean_text(tag.get_text(strip=True)) # Use clean_text
            logging.debug(f"{source_id} Extracted Link: {listing['link']}")
            logging.debug(f"{source_id} Extracted Name: {listing['name']}")

            if not listing['link'] or not listing['name']: logging.debug(f"{source_id} Skip: Missing link/name"); continue

            parent = tag.find_parent('td') # As in simple code
            if parent:
                logging.debug(f"{source_id} Found parent TD.")
                # Use find_next_sibling (more correct than find_next for siblings)
                features = parent.find_next_sibling('td', class_='realEstateBlock__features')
                if features:
                    logging.debug(f"{source_id} Found features sibling TD.")
                    sqm_match = re.search(r'(\d+)\s*m[²2q]', features.text, re.IGNORECASE) # Robust sqm regex
                    if sqm_match:
                        listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
                else: logging.debug(f"{source_id} Features sibling TD not found.")

                price_tag = parent.find_next_sibling('td', class_='realEstateBlock__price')
                if price_tag:
                    logging.debug(f"{source_id} Found price sibling TD.")
                    # Use robust extract_number
                    listing['price'] = extract_number(price_tag.text, is_float=True)
                else: logging.debug(f"{source_id} Price sibling TD not found.")
            else: logging.debug(f"{source_id} Parent TD not found.")

            if listing['link'] and listing['name']:
                 results.append(listing)
                 logging.debug(f"{source_id} PARSED OK: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else: logging.debug(f"{source_id} Skipped: Missing essential data.")

        except Exception as e:
            logging.warning(f"{source_id} Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False)

    logging.info(f"{source_id} Successfully parsed {len(results)} listings from this email.")
    return results

def parse_casait(soup, received_time):
    """Parses Casa.it using selectors from the simple working example."""
    results = []
    source_id = "[Casa.it - Simple Parse]"
    # Selector from simple code
    link_tags = soup.find_all('a', href=re.compile(r'https://www\.casa\.it/immobili/'), style=re.compile(r'color:\s*#1A1F24', re.IGNORECASE))
    logging.debug(f"{source_id} Found {len(link_tags)} potential <a> tags.")

    for i, tag in enumerate(link_tags):
        logging.debug(f"{source_id} Processing link tag {i+1}/{len(link_tags)}")
        listing = {'source': 'casa.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            listing['link'] = tag.get('href')
            listing['name'] = clean_text(tag.get_text(strip=True)) # Use clean_text
            logging.debug(f"{source_id} Extracted Link: {listing['link']}")
            logging.debug(f"{source_id} Extracted Name: {listing['name']}")

            if not listing['link'] or not listing['name']: logging.debug(f"{source_id} Skip: Missing link/name"); continue

            # Using find_parent() and find_next() as in simple code - BEWARE, this might be fragile/incorrect
            # It's better to use find_next_sibling if elements are siblings, or more specific container finding
            parent = tag.parent # Simple code used find_parent() - let's try tag.parent first
            if not parent: logging.debug(f"{source_id} No parent found for tag."); continue

            # Use find_all on parent instead of find_next, which isn't for siblings
            # Find size span based on style (fragile)
            size_tag = parent.find('span', style=re.compile(r'padding-right:\s*10px', re.IGNORECASE))
            if size_tag:
                logging.debug(f"{source_id} Found potential size tag.")
                sqm_match = re.search(r'(\d+)', size_tag.text) # Simple regex from original
                if sqm_match:
                    listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else: logging.debug(f"{source_id} Size span with padding not found.")

            # Find price span based on style (fragile)
            price_tag = parent.find('span', style=re.compile(r'font-weight:\s*bold', re.IGNORECASE))
            if price_tag:
                logging.debug(f"{source_id} Found potential price tag.")
                # Use robust extract_number
                listing['price'] = extract_number(price_tag.text, is_float=True)
            else: logging.debug(f"{source_id} Price span with bold font not found.")

            if listing['link'] and listing['name']:
                 results.append(listing)
                 logging.debug(f"{source_id} PARSED OK: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else: logging.debug(f"{source_id} Skipped: Missing essential data.")

        except Exception as e:
            logging.warning(f"{source_id} Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False)

    logging.info(f"{source_id} Successfully parsed {len(results)} listings from this email.")
    return results

def parse_idealista(soup, received_time):
    """Parses Idealista.it using selectors based on provided snippet."""
    # This uses the logic from the previous debug version, as it wasn't in the simple code
    results = []
    source_id = "[Idealista - Debug Parse]"
    link_tags = soup.find_all('a', href=re.compile(r'https://www\.idealista\.it/immobile/'), style=re.compile(r'color:\s*#2172b2', re.IGNORECASE))
    logging.debug(f"{source_id} Found {len(link_tags)} potential <a> tags.")

    for i, link_tag in enumerate(link_tags):
        logging.debug(f"{source_id} Processing link tag {i+1}/{len(link_tags)}")
        listing = {'source': 'idealista.it', 'name': None, 'link': None, 'square_meters': None, 'price': None, 'price_per_sqm': None, 'location': '', 'received_time': received_time, 'extracted_time': datetime.now(timezone.utc).isoformat(), 'score': 0.0}
        try:
            listing['link'] = link_tag.get('href')
            listing['name'] = clean_text(link_tag.get_text(strip=True))
            logging.debug(f"{source_id} Extracted Link: {listing['link']}")
            logging.debug(f"{source_id} Extracted Name: {listing['name']}")

            if not listing['link'] or not listing['name']: logging.debug(f"{source_id} Skip: Missing link/name"); continue

            container = link_tag.find_parent('td') or link_tag.find_parent('div') or link_tag.parent
            if not container: logging.warning(f"{source_id} Could not find container for link: {listing['link']}"); container = soup

            # Price finding (style or regex fallback)
            price_tag = container.find('span', style=lambda s: s and 'font-weight: bold' in s.lower() and 'font-size' in s.lower())
            if price_tag:
                 logging.debug(f"{source_id} Found potential price tag via style."); listing['price'] = extract_number(price_tag.get_text(strip=True), is_float=True)
            else:
                  price_match = re.search(r'([\d\.,]+)\s*€', container.get_text(" ", strip=True))
                  if price_match: logging.debug(f"{source_id} Found price via regex fallback."); listing['price'] = extract_number(price_match.group(1), is_float=True)
                  else: logging.debug(f"{source_id} Price tag/pattern not found.")

            # Size finding (div with m² or regex fallback)
            size_div = container.find('div', string=re.compile(r'\d+\s*m[²2q]', re.IGNORECASE))
            if size_div:
                logging.debug(f"{source_id} Found potential size div.")
                sqm_match = re.search(r'(\d+)\s*m[²2q]', size_div.get_text(strip=True), re.IGNORECASE)
                if sqm_match: listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
            else:
                 sqm_match = re.search(r'(\d+)\s*m[²2q]', container.get_text(" ", strip=True), re.IGNORECASE)
                 if sqm_match: logging.debug(f"{source_id} Found size via regex fallback."); listing['square_meters'] = extract_number(sqm_match.group(1), is_float=False)
                 else: logging.debug(f"{source_id} Size div/pattern not found.")

            if listing['link'] and listing['name']:
                 results.append(listing)
                 logging.debug(f"{source_id} PARSED OK: {listing['name'][:30]}... Price: {listing['price']}, SqM: {listing['square_meters']}")
            else: logging.debug(f"{source_id} Skipped: Missing essential data.")

        except Exception as e:
            logging.warning(f"{source_id} Parsing block failed: {e}. Link: {listing.get('link') or 'N/A'}", exc_info=False)

    logging.info(f"{source_id} Successfully parsed {len(results)} listings from this email.")
    return results

# --- Processing and Filtering (Unchanged: validate_and_enrich_listing, compute_scores ) ---
def validate_and_enrich_listing(listing):
    # Uses the refined validation from previous debug version
    link_for_log = listing.get('link', 'No Link'); name_for_log = listing.get('name', 'No Name')[:60] + "..."
    if listing.get('price') is None or listing.get('square_meters') is None: logging.debug(f"Validation fail: Missing Price/SqM - {name_for_log}"); return None
    name_lower = listing['name'].lower()
    if any(bad in name_lower for bad in BAD_KEYWORDS): keyword_found = next((bad for bad in BAD_KEYWORDS if bad in name_lower), ''); logging.debug(f"Validation fail: Bad keyword '{keyword_found}' - {name_for_log}"); return None
    sqm = listing['square_meters']
    if not isinstance(sqm, (int, float)) or not (MIN_SQUARE_METERS <= sqm <= MAX_SQUARE_METERS): logging.debug(f"Validation fail: Sqm out of range ({sqm}) - {name_for_log}"); return None
    price = listing['price']
    if not isinstance(price, (int, float)) or price <= 0 or sqm <=0 : logging.debug(f"Validation fail: Invalid price ({price}) or sqm ({sqm}) - {name_for_log}"); return None
    try: price_per_sqm = round(price / sqm, 2)
    except ZeroDivisionError: logging.debug(f"Validation fail: Zero Division Error ({sqm}) - {name_for_log}"); return None
    listing['price_per_sqm'] = price_per_sqm
    if price_per_sqm < MIN_PRICE_PER_SQM: logging.debug(f"Validation fail: Price/sqm too low ({price_per_sqm:.0f}) - {name_for_log}"); return None
    try:
        received_dt = datetime.fromisoformat(listing['received_time'])
        if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE: logging.debug(f"Validation fail: Listing too old ({listing['received_time']}) - {name_for_log}"); return None
    except (ValueError, TypeError) as e: logging.warning(f"⚠️ Invalid received_time '{listing.get('received_time')}' for {link_for_log}: {e}"); return None
    try: # Location extraction
        parts = listing['name'].split(',')
        if len(parts) > 1: loc_raw=parts[-1].strip(); loc_cln=re.sub(r'\(\w{2}\)$','',loc_raw).strip(); loc_cln=re.sub(r'^\d{5}\s*','',loc_cln).strip(); listing['location']=loc_cln if loc_cln else "Unknown"
        elif ' in ' in listing['name']: loc_raw=listing['name'].split(' in ')[-1].strip(); loc_cln=re.sub(r'\(\w{2}\)$','',loc_raw).strip(); listing['location']=loc_cln if loc_cln else "Unknown"
        else: listing['location']="Unknown"
    except Exception as e: logging.warning(f"⚠️ Error extracting location from '{listing['name']}': {e}"); listing['location']="Error"
    logging.debug(f"Validation OK: {name_for_log} ({listing['price_per_sqm']:.0f} €/mq)")
    return listing

def compute_scores(listings):
    # (compute_scores logic remains the same)
    if not listings: return []
    valid = []; [ (valid.append(l) if isinstance(l.get('price_per_sqm'),(int,float)) and isinstance(l.get('received_time'), str) and datetime.fromisoformat(l['received_time']) else setattr(l,'score',0.0)) for l in listings]
    if not valid: logging.warning("⚠️ No listings valid for scoring."); [setattr(l,'score',0.0) for l in listings if 'score' not in l]; return listings
    prices=[l['price_per_sqm'] for l in valid]; times=[datetime.fromisoformat(l['received_time']).timestamp() for l in valid]
    min_p,max_p=min(prices),max(prices); p_range=max_p-min_p if max_p>min_p else 1.0
    min_t,max_t=min(times),max(times); t_range=max_t-min_t if max_t>min_t else 1.0
    map_l={l['link']:l for l in valid}
    for l in listings:
        link=l.get('link');
        if link in map_l:
            sl=map_l[link]; pps=sl['price_per_sqm']; ts=datetime.fromisoformat(sl['received_time']).timestamp()
            try: np=(pps-min_p)/p_range; inp=1.0-np; nt=(ts-min_t)/t_range; l['score']=round(PRICE_WEIGHT*inp+RECENCY_WEIGHT*nt,4)
            except Exception as e: logging.warning(f"⚠️ Score calc error {link}: {e}"); l['score']=0.0
        else: l.setdefault('score', 0.0)
    listings.sort(key=lambda l: l.get('score',0.0), reverse=True)
    logging.info(f"📊 Computed scores for {len(valid)} listings.")
    return listings

# --- Main Execution Logic (Unchanged - process all, overwrite) ---

def scrape_emails():
    """
    Main flow: Connects, fetches ALL matching emails, parses (using simple selectors + Idealista),
    filters, deduplicates (within run), scores, saves (OVERWRITES file). Uses html.parser.
    """
    mail = None
    all_found_listings_this_run = []
    seen_links_this_run = set()
    first_email_processed = True # Still useful for saving debug files

    try:
        mail = connect_to_mail()
        mail.select('inbox')

        logging.info(f"🔍 Searching mailbox for ALL emails with query: {EMAIL_SEARCH_QUERY}")
        try:
            status, data = mail.search(None, EMAIL_SEARCH_QUERY)
            if status != 'OK': logging.error(f"❌ Mailbox search failed: {status}"); return
            if not data or not data[0].strip(): logging.info("✅ No emails matching criteria found."); save_listings([]); return
            email_ids = data[0].split()
        except Exception as e: logging.error(f"❌ Error during IMAP search: {e}"); return

        logging.info(f"Found {len(email_ids)} emails matching criteria. Processing all...")

        processed_emails = 0
        total_valid_added_this_run = 0

        for eid in email_ids:
            eid_str = eid.decode()
            processed_emails += 1
            logging.info(f"-- Processing email {processed_emails}/{len(email_ids)} (ID: {eid_str})...")
            email_processed_successfully = False

            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK': logging.warning(f"⚠️ Fetch failed {eid_str}: {status}"); continue
                if not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2: logging.warning(f"⚠️ Bad fetch data {eid_str}"); continue

                # --- DEBUG: Save Raw Email for first email ---
                if first_email_processed:
                    # (Save raw email logic - same as before)
                    try: raw_filename=f"email_{eid_str}_raw.eml"; f_raw=open(raw_filename,"wb"); f_raw.write(msg_data[0][1]); f_raw.close(); logging.info(f"💾 SAVED raw: {raw_filename}")
                    except Exception as e_sr: logging.error(f"❌ Failed save raw {eid_str}: {e_sr}")
                # --- End DEBUG ---

                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)

                # Extract received time (same logic as before)
                received_time_str=msg['Date']; received_dt_naive=utils.parsedate_to_datetime(received_time_str)
                if received_dt_naive:
                    if received_dt_naive.tzinfo is None or received_dt_naive.tzinfo.utcoffset(received_dt_naive) is None: rda=received_dt_naive.replace(tzinfo=timezone.utc)
                    else: rda=received_dt_naive.astimezone(timezone.utc)
                    received_time_iso=rda.isoformat()
                else: logging.warning(f"⚠️ Bad date '{received_time_str}'. Using current."); received_time_iso=datetime.now(timezone.utc).isoformat()

                # Find HTML part using get_payload(decode=True)
                html_body = None
                if msg.is_multipart():
                    for part in msg.walk():
                         ct = part.get_content_type(); cd = str(part.get("Content-Disposition"))
                         logging.debug(f"Email Part: CT={ct}, CD={cd}")
                         if ct == 'text/html' and 'attachment' not in cd:
                             try:
                                 charset = part.get_content_charset() or 'utf-8'; payload = part.get_payload(decode=True)
                                 html_body = payload.decode(charset, errors='replace'); logging.debug(f"Found HTML part, len {len(html_body)}, charset {charset}."); break
                             except Exception as e: html_body = None; logging.warning(f"⚠️ Error decoding HTML part: {e}")
                else: # Non-multipart
                     if msg.get_content_type() == 'text/html':
                          try: charset = msg.get_content_charset() or 'utf-8'; payload = msg.get_payload(decode=True); html_body = payload.decode(charset, errors='replace'); logging.debug("Found HTML (non-multipart).")
                          except Exception as e: logging.warning(f"⚠️ Error decoding non-multipart HTML: {e}")

                if not html_body: logging.warning(f"⚠️ No suitable HTML body in {eid_str}."); email_processed_successfully = True; continue

                # --- DEBUG: Save Extracted HTML for first email ---
                if first_email_processed:
                    # (Save extracted HTML logic - same as before)
                    try: html_filename=f"email_{eid_str}_extracted.html"; f_html=open(html_filename,"w",encoding='utf-8',errors='replace'); f_html.write(html_body); f_html.close(); logging.info(f"💾 SAVED HTML: {html_filename}")
                    except Exception as e_sh: logging.error(f"❌ Failed save HTML {eid_str}: {e_sh}")
                # --- End DEBUG ---

                # Parse HTML using html.parser
                logging.debug("Parsing HTML body with BeautifulSoup (html.parser)...")
                try:
                    soup = BeautifulSoup(html_body, 'html.parser') # <--- USE html.parser
                except Exception as e:
                    logging.error(f"❌ BeautifulSoup (html.parser) failed for {eid_str}: {e}")
                    continue # Skip email if BS fails

                # --- Extract listings using ALL parsers ---
                logging.debug(f"Running parsers for {eid_str}...")
                potential_listings = []
                potential_listings.extend(parse_casait(soup, received_time_iso))
                potential_listings.extend(parse_immobiliare(soup, received_time_iso))
                potential_listings.extend(parse_idealista(soup, received_time_iso)) # Keep Idealista parser
                logging.info(f"Email ID {eid_str}: Extracted {len(potential_listings)} potential listings total.")

                # --- Validate, enrich, check duplicates (within run), and add ---
                listings_added_from_email = 0
                for potential in potential_listings:
                    link = potential.get('link')
                    if link and link in seen_links_this_run: logging.debug(f"Dup (Run): Link {link}"); continue
                    if not link: logging.debug(f"Skip: no link."); continue
                    valid = validate_and_enrich_listing(potential) # Renamed variable
                    if not valid: continue
                    similar = False
                    for existing in all_found_listings_this_run: # Renamed variable
                        if are_names_similar(valid['name'], existing.get('name','')): logging.debug(f"Dup (Run): Similar name! '{valid['name'][:50]}...'"); similar=True; break
                    if similar: continue
                    all_found_listings_this_run.append(valid)
                    seen_links_this_run.add(link)
                    total_valid_added_this_run += 1
                    listings_added_from_email += 1
                    logging.debug(f"➕ Staged NEW listing ({valid['source']}): {valid['name'][:70]}...")

                logging.info(f"Email ID {eid_str}: Staged {listings_added_from_email} new valid/unique listings.")
                email_processed_successfully = True

            except Exception as e:
                logging.error(f"❌❌ Unhandled error processing email ID {eid_str}: {e}", exc_info=True)

            finally: # Mark as Seen
                 if email_processed_successfully:
                     try:
                         status, _ = mail.store(eid, '+FLAGS', '\\Seen');
                         if status == 'OK': logging.debug(f"Marked {eid_str} as Seen.")
                         else: logging.warning(f"⚠️ Failed mark {eid_str} as Seen: {status}")
                     except Exception as e: logging.warning(f"⚠️ Exception marking {eid_str} as Seen: {e}")
                 else: logging.warning(f"Email {eid_str} not marked Seen (errors).")
            first_email_processed = False # Only save files for the first one

        # --- Final Processing ---
        logging.info(f"-- Finished processing all {processed_emails} emails.")
        logging.info(f"-- Found {total_valid_added_this_run} unique, valid listings in this run.")
        if total_valid_added_this_run > 0:
            logging.info("Calculating scores...")
            all_listings_scored = compute_scores(all_found_listings_this_run)
            save_listings(all_listings_scored)
        else:
            logging.info("No valid listings found. Saving empty file."); save_listings([])

    except imaplib.IMAP4.error as e: logging.critical(f"💥 IMAP Error: {e}", exc_info=True)
    except Exception as e: logging.critical(f"💥 Unhandled exception: {e}", exc_info=True)
    finally:
        if mail:
            try: mail.logout(); logging.info("🚪 Logged out.")
            except Exception as e: logging.warning(f"⚠️ Error during logout: {e}")

if __name__ == '__main__':
    logging.info("🚀 Starting email scraping process (Mode: Process All, Overwrite, Simple Selectors + Idealista, html.parser)...")
    scrape_emails()
    logging.info("🏁 Scraping process finished.")

































































