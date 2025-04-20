import imaplib
import email
from email import policy, utils
from bs4 import BeautifulSoup
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re
import pandas as pd

# Load environment variables from a .env file
load_dotenv()

IMAP_SERVER = 'imapmail.libero.it'
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
LISTINGS_FILE = 'listings.json'
OUTPUT_HTML = 'docs/index.html'

BAD_KEYWORDS = ['stazione', 'asta', 'affitto']
MAX_SQUARE_METERS = 105
MAX_LISTING_AGE = timedelta(days=30)

def connect_mail():
    """Connects to the IMAP server."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("✅ Connected to email")
        return mail
    except imaplib.IMAP4.error as e:
        print(f"❌ Connection error: {e}")
        raise

def load_listings():
    """Loads existing listings from a JSON file."""
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
    """Saves the current listings to a JSON file."""
    try:
        with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error saving listings: {e}")

def parse_email(body, received_time):
    """Parses the email body to extract listing information."""
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
        # Extract name and link
        name_tag = soup.find('a', href=re.compile(r'https://clicks\.immobiliare\.it/'), style=re.compile(r'color:\s*#0074c1'))
        if name_tag:
            data['name'] = name_tag.text.strip()
            data['link'] = name_tag['href']

        # Extract square meters
        sqm_tag = soup.find('td', class_='realEstateBlock__features')
        if sqm_tag:
            sqm_match = re.search(r'(\d+)\s*m²', sqm_tag.text)
            if sqm_match:
                data['square_meters'] = int(sqm_match.group(1))

        # Extract price
        price_tag = soup.find('td', class_='realEstateBlock__price')
        if price_tag:
            # Handle potential commas and periods in price
            price_text = price_tag.text.replace('.', '').replace(',', '.')
            price_match = re.search(r'€\s*([\d\.]+)', price_text)
            if price_match:
                data['price'] = float(price_match.group(1))

        # Extract location from name
        data['location'] = data['name'].split(',')[-1].strip() if ',' in data['name'] else 'Unknown'

        # Apply filters
        if any(bad_word in data['name'].lower() for bad_word in BAD_KEYWORDS):
            return None

        if not data['square_meters'] or not data['price']:
             return None # Skip if essential data is missing

        if data['square_meters'] > MAX_SQUARE_METERS:
            return None

        # Check listing age
        try:
            received_dt = datetime.fromisoformat(received_time)
            if datetime.now(timezone.utc) - received_dt > MAX_LISTING_AGE:
                return None
        except ValueError:
             print(f"⚠️ Could not parse received time: {received_time}")
             return None # Skip if received time is invalid


        return data

    except Exception as e:
        print(f"❌ Error parsing email: {e}")
        return None

def rank_listings(listings):
    """Ranks listings based on price per square meter and age."""
    current_time = datetime.now(timezone.utc)
    for listing in listings:
        listing['price_per_sqm'] = listing['price'] / listing['square_meters'] if listing.get('price') is not None and listing.get('square_meters') else float('inf')
        try:
            received_time = datetime.fromisoformat(listing['received_time'])
            listing['time_delta'] = (current_time - received_time).total_seconds()
        except ValueError:
             listing['time_delta'] = float('inf') # Assign high time_delta for invalid times

        # Calculate score (adjust weights as needed)
        price_score = 1 / (listing['price_per_sqm'] + 1) if listing['price_per_sqm'] != float('inf') else 0
        time_score = 1 / (listing['time_delta'] / 3600 + 1) if listing['time_delta'] != float('inf') else 0
        listing['score'] = round(price_score * 0.7 + time_score * 0.3, 4) # 70% price, 30% recency

    sorted_list = sorted(listings, key=lambda x: -x['score'])
    for i, listing in enumerate(sorted_list):
        listing['rank'] = i + 1
    return sorted_list

def generate_html(listings):
    """Generates an HTML file for visualizing the ranked listings."""
    if not listings:
        print("No listings to generate HTML.")
        return

    df = pd.DataFrame(listings)
    # Select and order columns for the table
    df = df[['rank', 'name', 'location', 'price', 'square_meters', 'price_per_sqm', 'link']]

    # Format currency and price per sqm
    df['price'] = df['price'].apply(lambda x: f"€{x:,.0f}" if pd.notnull(x) else 'N/A').str.replace(',', '.', regex=False)
    df['price_per_sqm'] = df['price_per_sqm'].apply(lambda x: f"€{x:,.2f}" if pd.notnull(x) and x != float('inf') else 'N/A').str.replace(',', '.', regex=False)
    df['square_meters'] = df['square_meters'].apply(lambda x: f"{x:.0f} m²" if pd.notnull(x) else 'N/A')

    # Create clickable links
    df['link'] = df['link'].apply(lambda x: f'<a href="{x}" target="_blank">Link</a>' if pd.notnull(x) else 'N/A')

    # Generate HTML table from DataFrame
    html_table = df.to_html(index=False, escape=False, justify='center', classes='styled-table')

    # Basic styling for the HTML table
    html_content = f"""
    <html>
    <head>
        <title>HouseX Rankings</title>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 2em; color: #333; }}
            .container {{ max-width: 1000px; margin: 0 auto; background-color: #fff; padding: 2em; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            h1 {{ text-align: center; color: #0056b3; margin-bottom: 1em; }}
            .styled-table {{ border-collapse: collapse; margin: 25px auto; font-size: 0.9em; width: 100%; box-shadow: 0 0 20px rgba(0, 0, 0, 0.15); }}
            .styled-table thead tr {{ background-color: #009879; color: #ffffff; text-align: left; }}
            .styled-table th, .styled-table td {{ padding: 12px 15px; text-align: left; }}
            .styled-table tbody tr {{ border-bottom: 1px solid #dddddd; }}
            .styled-table tbody tr:nth-of-type(even) {{ background-color: #f3f3f3; }}
            .styled-table tbody tr:last-of-type {{ border-bottom: 2px solid #009879; }}
            .styled-table tbody tr.active-row {{ font-weight: bold; color: #009879; }}
            .styled-table a {{ color: #0074c1; text-decoration: none; }}
            .styled-table a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏡 House Listings Ranking</h1>
            {html_table}
        </div>
    </body>
    </html>
    """

    # Create the output directory if it doesn't exist
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ Website updated at {OUTPUT_HTML}")


def scrape_immobiliare_emails():
    """Scrapes emails from Immobiliare.it, parses listings, ranks them, and updates the HTML file."""
    mail = None
    try:
        mail = connect_mail()
        mail.select('inbox')
        # Search for emails from the specific sender
        status, data = mail.search(None, '(FROM "noreply@notifiche.immobiliare.it")')
        if status != 'OK' or not data or not data[0].strip():
            print("No emails found from noreply@notifiche.immobiliare.it.")
            return

        ids = data[0].split()
        listings = load_listings()
        # Create a set of existing listing keys for efficient lookup
        existing_keys = {(l.get('name'), l.get('location'), l.get('square_meters')) for l in listings if l.get('name') and l.get('location') and l.get('square_meters') is not None}
        new_listings_found = 0
        processed_uids = set() # To keep track of processed UIDs

        # Iterate through emails in reverse order (newest first) - though IMAP results might not be guaranteed in order
        # Fetching in reverse might be more efficient if you stop after finding existing listings
        # For simplicity, processing in the order returned by search for now.
        # If performance is an issue, fetching UIDs and processing from newest could be an improvement.

        for email_id in ids:
             # Use UID FETCH for more reliable fetching and tracking if needed
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                print(f"❌ Failed to fetch email ID {email_id}. Status: {status}")
                continue

            raw_email = msg_data[0][1]
            message = email.message_from_bytes(raw_email, policy=policy.default)

            # Extract received time
            received_time = message.get('Date')
            if received_time:
                try:
                    parsed_time = utils.parsedate_tz(received_time)
                    if parsed_time:
                        received_time_iso = datetime.fromtimestamp(utils.mktime_tz(parsed_time), tz=timezone.utc).isoformat()
                    else:
                         received_time_iso = datetime.now(timezone.utc).isoformat() # Fallback to now if parsing fails
                         print(f"⚠️ Could not parse date from email ID {email_id}: {received_time}. Using current time.")
                except Exception as e:
                     received_time_iso = datetime.now(timezone.utc).isoformat() # Fallback to now if parsing fails
                     print(f"❌ Error parsing date from email ID {email_id}: {received_time}. Using current time. Error: {e}")
            else:
                 received_time_iso = datetime.now(timezone.utc).isoformat() # Fallback to now if no date header
                 print(f"⚠️ No date header in email ID {email_id}. Using current time.")


            # Get the email body
            body = None
            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition'))

                    # Look for the HTML part
                    if content_type == 'text/html' and 'attachment' not in content_disposition:
                        try:
                            body = part.get_content()
                            break # Found HTML body, no need to check other parts
                        except Exception as e:
                            print(f"❌ Error getting content from HTML part in email ID {email_id}: {e}")
            elif message.get_content_type() == 'text/html':
                 try:
                    body = message.get_content()
                 except Exception as e:
                    print(f"❌ Error getting content from HTML email ID {email_id}: {e}")


            if body:
                new_listing_data = parse_email(body, received_time_iso)
                if new_listing_data:
                    listing_key = (new_listing_data.get('name'), new_listing_data.get('location'), new_listing_data.get('square_meters'))
                    # Add listing if essential data is present and it's not a duplicate
                    if all(v is not None for v in listing_key) and listing_key not in existing_keys:
                        # Check if a listing with the same link already exists (alternative duplicate check)
                        if not any(l.get('link') == new_listing_data['link'] for l in listings):
                             listings.append(new_listing_data)
                             existing_keys.add(listing_key)
                             new_listings_found += 1
                             print(f"➕ Added new listing: {new_listing_data.get('name')}")
                        else:
                            print(f"Skipping potential duplicate based on link: {new_listing_data.get('link')}")
                    else:
                         # Optional: print why a listing was skipped
                         if all(v is not None for v in listing_key) and listing_key in existing_keys:
                             print(f"Skipping existing listing: {new_listing_data.get('name')}")
                         else:
                             print(f"Skipping listing due to missing essential data or filters: {new_listing_data.get('name')}")


        if new_listings_found > 0 or not os.path.exists(OUTPUT_HTML) or os.path.getmtime(OUTPUT_HTML) < os.path.getmtime(LISTINGS_FILE):
            # Only re-rank and generate HTML if new listings were found or if HTML is outdated
            print("✨ Ranking listings...")
            listings = rank_listings(listings)
            save_listings(listings)
            generate_html(listings)
            if new_listings_found > 0:
                print(f"✅ Found and added {new_listings_found} new listings.")
            else:
                print("✅ Listings re-ranked and HTML regenerated.")
        else:
            print("No new listings added and HTML is up to date.")


    except Exception as e:
        print(f"❌ An error occurred during the scraping process: {e}")
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
                print("📧 Disconnected from email")
            except Exception as e:
                print(f"Logout error: {e}")

if __name__ == '__main__':
    if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
        print("Please set EMAIL_ACCOUNT and EMAIL_PASSWORD environment variables.")
    else:
        scrape_immobiliare_emails()
