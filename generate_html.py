# generate_html.py
import json
from pathlib import Path

with open('listings.json', encoding='utf-8') as f:
    listings = json.load(f)

listings = sorted(listings, key=lambda x: -x.get('score', 0))

<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>HouseX2 - Listings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f9f9f9; }
    h1 { text-align: center; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 10px; border: 1px solid #ccc; text-align: center; }
    th { background-color: #f0f0f0; }
    a { color: #0077cc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .highlight { background-color: #d7ffd7; }
    .filter { margin: 10px 0; }
    canvas { margin-top: 40px; }
  </style>
</head>
<body>

  <h1>🏠 HouseX2 Listings</h1>
  <div style="text-align:center; margin-bottom:20px;">
    <strong>Updated:</strong> <span id="update-date"></span>
  </div>

  <div class="filter">
    <label>Min Square Meters: <input type="number" id="min-sqm" value="0" /></label>
    <label style="margin-left: 20px;">Max Price: <input type="number" id="max-price" value="1000000" /></label>
    <button onclick="applyFilters()">Apply Filters</button>
  </div>

  <h2>🥇 Top 5 Deals</h2>
  <table id="top-table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Price</th>
        <th>Sqm</th>
        <th>€/m²</th>
        <th>Score</th>
        <th>Link</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>

  <h2>📋 All Listings</h2>
  <table id="listings-table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Price</th>
        <th>Sqm</th>
        <th>€/m²</th>
        <th>Location</th>
        <th>Received</th>
        <th>Score</th>
        <th>Link</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>

  <canvas id="priceChart" width="400" height="200"></canvas>

  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    let listings = [];

    function loadListings() {
      fetch('listings.json')
        .then(response => response.json())
        .then(data => {
          listings = data;
          document.getElementById('update-date').innerText = new Date().toLocaleString();
          populateTables();
          drawChart();
        })
        .catch(error => {
          console.error('Error loading listings:', error);
        });
    }

    function populateTables() {
      const topBody = document.querySelector('#top-table tbody');
      const allBody = document.querySelector('#listings-table tbody');
      topBody.innerHTML = '';
      allBody.innerHTML = '';

      const sorted = [...listings].sort((a, b) => b.score - a.score);

      sorted.slice(0, 5).forEach(listing => {
        topBody.innerHTML += renderRow(listing, true);
      });

      listings.forEach(listing => {
        allBody.innerHTML += renderRow(listing);
      });
    }

    function renderRow(listing, highlight = false) {
      const pricePerSqm = (listing.price / listing.square_meters).toFixed(0);
      return `<tr class="${highlight ? 'highlight' : ''}">
        <td>${listing.name}</td>
        <td>€${listing.price.toLocaleString()}</td>
        <td>${listing.square_meters}</td>
        <td>€${pricePerSqm}</td>
        <td>${listing.location || '-'}</td>
        <td>${new Date(listing.received_time).toLocaleDateString()}</td>
        <td>${(listing.score * 100).toFixed(0)}</td>
        <td><a href="${listing.link}" target="_blank">View</a></td>
      </tr>`;
    }

    function applyFilters() {
      const minSqm = parseInt(document.getElementById('min-sqm').value) || 0;
      const maxPrice = parseInt(document.getElementById('max-price').value) || Infinity;
      const filtered = listings.filter(l => l.square_meters >= minSqm && l.price <= maxPrice);

      const allBody = document.querySelector('#listings-table tbody');
      allBody.innerHTML = '';
      filtered.forEach(listing => {
        allBody.innerHTML += renderRow(listing);
      });
    }

    function drawChart() {
      const ctx = document.getElementById('priceChart').getContext('2d');
      const pricesPerSqm = listings.map(l => l.price / l.square_meters);
      new Chart(ctx, {
        type: 'bar',
        data: {
          labels: listings.map((_, i) => i + 1),
          datasets: [{
            label: '€/m²',
            data: pricesPerSqm,
            backgroundColor: '#4caf50',
          }]
        },
        options: {
          scales: {
            y: { beginAtZero: true }
          }
        }
      });
    }

    loadListings();
  </script>
</body>
</html>


Path("docs").mkdir(exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("✅ Generated docs/index.html")
