# generate_html.py
import json
from pathlib import Path

with open('listings.json', encoding='utf-8') as f:
    listings = json.load(f)

listings = sorted(listings, key=lambda x: -x.get('score', 0))

html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>🏠 House Listings</title>
  <style>
    body { font-family: sans-serif; padding: 20px; background: #f5f5f5; }
    h1 { text-align: center; }
    .card {
      background: white;
      padding: 16px;
      margin-bottom: 16px;
      border-radius: 10px;
      box-shadow: 0 0 10px rgba(0,0,0,0.1);
    }
    .score { float: right; font-weight: bold; color: #2b7a78; }
    .link { text-decoration: none; color: #17252a; }
    .meta { color: #555; font-size: 0.9em; margin-top: 4px; }
  </style>
</head>
<body>
  <h1>🏠 Ranked House Listings</h1>
"""

for l in listings:
    html += f"""
    <div class="card">
      <a href="{l['link']}" class="link" target="_blank">{l['name']}</a>
      <span class="score">Score: {l['score']:.2f}</span>
      <div class="meta">📍 {l.get('location', 'N/A')} | {l['square_meters']} m² | €{l['price']:,.0f}</div>
    </div>
    """

html += """
</body>
</html>
"""

Path("docs").mkdir(exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("✅ Generated docs/index.html")
