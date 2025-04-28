import json
from pathlib import Path
from datetime import datetime, timezone
import math

# --- Configuration ---
LISTINGS_FILE = 'listings.json'
OUTPUT_DIR = 'docs'
OUTPUT_FILE = 'index.html'
NOW_UTC = datetime.now(timezone.utc)
GENERATED_DATE_STR = NOW_UTC.strftime("%Y-%m-%d %H:%M:%S %Z")

# --- Helper Functions ---
def format_currency(value):
    """Formats currency nicely."""
    try:
        return f"€{value:,.0f}"
    except (ValueError, TypeError):
        return "N/A"

def format_sqm(value):
    """Formats square meters."""
    try:
        return f"{int(value)} m²"
    except (ValueError, TypeError):
        return "N/A"

def format_price_per_sqm(value):
    """Formats price per square meter."""
    try:
        return f"€{value:,.2f}/m²"
    except (ValueError, TypeError):
        return "N/A"

def format_datetime_readable(iso_str):
    """Formats ISO datetime string to a more readable format. Handles potential errors."""
    if not iso_str: return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str)
        # Convert to local timezone for display (optional, requires tzlocal library)
        # try:
        #     import tzlocal
        #     local_tz = tzlocal.get_localzone()
        #     dt = dt.astimezone(local_tz)
        # except ImportError:
        #      pass # Keep UTC if tzlocal not installed
        return dt.strftime("%Y-%m-%d %H:%M") # Example format
    except (ValueError, TypeError):
        return iso_str # Return original if parsing fails

def get_score_color(score):
    """Assigns a color based on the score (0-1)."""
    if score is None: return "#cccccc" # Grey for unknown
    # Simple gradient: Red (0) -> Yellow (0.5) -> Green (1.0)
    try:
        score = float(score)
        red = int(255 * max(0, 1 - 2 * score))
        green = int(255 * max(0, 2 * score - 1))
        blue = 0
        # Make yellow transition smoother
        if score < 0.5:
             green = int(255 * (2 * score)) # Ramp up green towards yellow
             red = 255
        else:
             green = 255
             red = int(255 * (1 - (2*(score-0.5))) ) # Ramp down red from yellow
        return f"#{red:02x}{green:02x}{blue:02x}"
    except (ValueError, TypeError):
         return "#cccccc"


# --- Load Data ---
listings = []
try:
    with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
        listings = json.load(f)
except FileNotFoundError:
    print(f"❌ Error: {LISTINGS_FILE} not found.")
    # Create empty HTML page? Or exit?
    listings = []
except json.JSONDecodeError:
    print(f"❌ Error: Could not decode JSON from {LISTINGS_FILE}.")
    listings = []
except Exception as e:
    print(f"❌ An unexpected error occurred loading {LISTINGS_FILE}: {e}")
    listings = []


# --- Calculate Summary Statistics ---
total_listings = len(listings)
valid_prices_sqm = [l.get('price_per_sqm') for l in listings if isinstance(l.get('price_per_sqm'), (int, float))]
average_price_sqm = sum(valid_prices_sqm) / len(valid_prices_sqm) if valid_prices_sqm else 0
average_price_sqm_str = format_price_per_sqm(average_price_sqm) if average_price_sqm else "N/A"


# --- Sort Listings (already done by score in scraper, but can re-sort) ---
# listings.sort(key=lambda x: x.get('score', 0.0), reverse=True)


# --- Generate HTML ---
html_head = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🏠 House Listings Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px; background-color: #f4f7f9; color: #333; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ text-align: center; color: #2c3e50; margin-bottom: 10px; }}
    .subtitle {{ text-align: center; color: #7f8c8d; margin-top: 0; margin-bottom: 30px; font-size: 0.9em; }}
    .summary {{ background-color: #eaf2f8; border-left: 5px solid #3498db; padding: 10px 15px; margin-bottom: 25px; font-size: 0.95em; border-radius: 4px; }}
    .summary p {{ margin: 5px 0; }}
    .summary strong {{ color: #2980b9; }}

    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}

    .card {{
      background: white;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
      padding: 15px 20px;
      display: flex;
      flex-direction: column;
      transition: box-shadow 0.2s ease-in-out;
    }}
    .card:hover {{ box-shadow: 0 5px 15px rgba(0,0,0,0.12); }}

    .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }}
    .card-title a {{
        text-decoration: none; color: #34495e; font-weight: 600; font-size: 1.1em;
        /* Prevent long titles from breaking layout */
        overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    }}
    .card-title a:hover {{ color: #3498db; }}

    .score-badge {{
        font-size: 0.9em; font-weight: bold; padding: 3px 8px; border-radius: 12px;
        color: white; white-space: nowrap; /* Prevent wrapping */
    }}

    .card-body {{ font-size: 0.9em; color: #555; margin-bottom: 15px; flex-grow: 1; }} /* Allow body to grow */
    .card-body strong {{ color: #333; }}
    .card-body .detail-item {{ margin-bottom: 5px; }}
    .card-body .detail-item .label {{ display: inline-block; width: 20px; text-align: center; margin-right: 5px; opacity: 0.7; }} /* Icons */

    .card-footer {{ font-size: 0.8em; color: #888; border-top: 1px solid #eee; padding-top: 10px; margin-top: auto; }} /* Push footer down */
    .card-footer span {{ display: inline-block; margin-right: 10px; }}

    /* Responsive adjustments */
    @media (max-width: 600px) {{
        body {{ padding: 10px; }}
        h1 {{ font-size: 1.5em; }}
        .card-grid {{ grid-template-columns: 1fr; }} /* Stack cards on small screens */
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🏠 House Listings Dashboard</h1>
    <p class="subtitle">Ranked by calculated score based on price/m² and recency.</p>

    <div class="summary">
      <p><strong>Total Listings:</strong> {total_listings}</p>
      <p><strong>Average Price:</strong> {average_price_sqm_str}</p>
      <p><strong>Generated:</strong> {GENERATED_DATE_STR}</p>
    </div>

    <div class="card-grid">
"""

html_cards = ""
if not listings:
    html_cards = "<p style='text-align: center; color: #777;'>No listings found or loaded.</p>"
else:
    for idx, l in enumerate(listings):
        score = l.get('score', 0.0)
        score_color = get_score_color(score)
        price_str = format_currency(l.get('price'))
        sqm_str = format_sqm(l.get('square_meters'))
        price_sqm_str = format_price_per_sqm(l.get('price_per_sqm'))
        received_str = format_datetime_readable(l.get('received_time'))
        last_seen_str = format_datetime_readable(l.get('last_seen_utc_iso'))

        html_cards += f"""
      <div class="card">
        <div class="card-header">
          <div class="card-title"><a href="{l.get('link', '#')}" target="_blank" title="{l.get('name', 'No Title')}">{l.get('name', 'No Title')}</a></div>
          <span class="score-badge" style="background-color: {score_color};" title="Score: {score:.4f}">
             {score:.2f}
          </span>
        </div>
        <div class="card-body">
          <div class="detail-item"><span class="label">💶</span><strong>Price:</strong> {price_str} ({price_sqm_str})</div>
          <div class="detail-item"><span class="label">📐</span><strong>Size:</strong> {sqm_str}</div>
          <div class="detail-item"><span class="label">📍</span><strong>Location:</strong> {l.get('location', 'N/A')}</div>
          <div class="detail-item"><span class="label">🏢</span><strong>Source:</strong> {l.get('source', 'N/A')}</div>
        </div>
        <div class="card-footer">
          <span><span title="Email Received Date">📥</span> {received_str}</span>
          <span><span title="Last Seen in Scrape">👀</span> {last_seen_str}</span>
        </div>
      </div>
      """

html_foot = """
    </div> </div> </body>
</html>
"""

# --- Write Output ---
output_path = Path(OUTPUT_DIR)
output_path.mkdir(exist_ok=True)
output_file_path = output_path / OUTPUT_FILE

try:
    with open(output_file_path, "w", encoding="utf-8") as f:
        f.write(html_head + html_cards + html_foot)
    print(f"✅ Generated {output_file_path}")
except Exception as e:
    print(f"❌ Error writing HTML file to {output_file_path}: {e}")
