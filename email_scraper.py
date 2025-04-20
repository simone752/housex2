import json
import os
from datetime import datetime, timezone
from bs4 import BeautifulSoup

LISTINGS_FILE = 'listings.json'
HTML_FILE = 'index.html'
MAX_LISTING_AGE_DAYS = 30

def load_listings():
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

def compute_scores(listings):
    prices = [l['price'] / l['square_meters'] for l in listings]
    times = [datetime.fromisoformat(l['received_time']).timestamp() for l in listings]

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(times), max(times)

    for i, listing in enumerate(listings):
        price_per_sqm = prices[i]
        timestamp = times[i]

        norm_price = (price_per_sqm - min_price) / (max_price - min_price) if max_price != min_price else 0
        norm_time = (timestamp - min_time) / (max_time - min_time) if max_time != min_time else 1

        score = 0.5 * (1 - norm_price) + 0.5 * norm_time
        listing['score'] = score

    return sorted(listings, key=lambda x: -x['score'])

def generate_html(listings):
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>House Listings</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        h1 { color: #333; }
        .listing { border-bottom: 1px solid #ccc; padding: 10px 0; }
        .listing a { font-size: 1.2em; color: #0074c1; text-decoration: none; }
        .meta { color: #555; font-size: 0.9em; }
    </style>
</head>
<body>
    <h1>🏠 Top Listings</h1>
'''
    for l in listings:
        price_per_sqm = l['price'] / l['square_meters']
        received_dt = datetime.fromisoformat(l['received_time']).astimezone(timezone.utc)
        received_str = received_dt.strftime('%Y-%m-%d %H:%M UTC')
        html += f'''
    <div class="listing">
        <a href="{l['link']}" target="_blank">{l['name']}</a><br>
        <div class="meta">
            {l['square_meters']} m² | €{l['price']:,.0f} | €{price_per_sqm:,.0f}/m² | {l['location']}<br>
            Received: {received_str}
        </div>
    </div>
'''
    html += '''
</body>
</html>'''
    return html

def update_html():
    listings = load_listings()
    if not listings:
        print("⚠️ No listings to display.")
        return

    # Filter out listings older than MAX_LISTING_AGE_DAYS
    now = datetime.now(timezone.utc)
    listings = [l for l in listings if (now - datetime.fromisoformat(l['received_time'])).days <= MAX_LISTING_AGE_DAYS]

    if not listings:
        print("⚠️ No recent listings to display.")
        return

    listings = compute_scores(listings)
    html_content = generate_html(listings)

    try:
        with open(HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ {HTML_FILE} updated with {len(listings)} listings.")
    except Exception as e:
        print(f"❌ Error writing to {HTML_FILE}: {e}")

if __name__ == '__main__':
    update_html()




