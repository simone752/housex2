import json
from datetime import datetime

# Load listings
with open("listings.json", "r", encoding="utf-8") as f:
    listings = json.load(f)

# Sort listings by score, highest first
listings.sort(key=lambda x: x.get("score", 0), reverse=True)

# Start HTML content
html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>🏠 House Listings</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 40px;
            background-color: #f9f9f9;
            color: #333;
        }}
        h1 {{
            text-align: center;
        }}
        .date-info {{
            text-align: center;
            font-size: 14px;
            margin-bottom: 30px;
            color: #666;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        th, td {{
            padding: 12px 15px;
            border: 1px solid #ccc;
            text-align: left;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
        }}
        tr:nth-child(even) {{
            background-color: #f2f2f2;
        }}
        .score-bar {{
            height: 12px;
            background: linear-gradient(90deg, #4CAF50, #8BC34A);
            border-radius: 5px;
        }}
    </style>
</head>
<body>

<h1>🏡 Best House Listings</h1>
<div class="date-info">Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>

<table>
    <thead>
        <tr>
            <th>🏠 Name</th>
            <th>📍 Location</th>
            <th>📏 Size (m²)</th>
            <th>💶 Price (€)</th>
            <th>📊 Score</th>
        </tr>
    </thead>
    <tbody>
"""

# Generate table rows
for listing in listings:
    name = listing.get("name", "N/A")
    url = listing.get("url", "#")
    location = listing.get("location", "Unknown")
    size = listing.get("size", "N/A")
    price = listing.get("price", "N/A")
    score = listing.get("score", 0)

    html_content += f"""
        <tr>
            <td><a href="{url}" target="_blank">{name}</a></td>
            <td>{location}</td>
            <td>{size}</td>
            <td>{price}</td>
            <td>
                <div style="width: {score}%; max-width: 100px;">
                    <div class="score-bar" style="width: {score}%"></div>
                </div>
                {score}
            </td>
        </tr>
    """

# End HTML content
html_content += """
    </tbody>
</table>

</body>
</html>
"""

# Save the file
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(html_content)

print("✅ HTML file generated: docs/index.html")
