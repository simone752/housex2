fetch('../listings.json')
  .then(res => res.json())
  .then(data => {
    const tbody = document.querySelector('#listings-table tbody');
    data.forEach(listing => {
      const tr = document.createElement('tr');

      const ageHours = Math.round((Date.now() - new Date(listing.received_time)) / 36e5);
      const pricePerSqm = Math.round(listing.price_per_sqm);

      let scoreClass = 'bad';
      if (listing.score > 0.005) scoreClass = 'okay';
      if (listing.score > 0.01) scoreClass = 'good';

      tr.innerHTML = `
        <td>${listing.rank}</td>
        <td>${listing.name}</td>
        <td>${listing.price.toLocaleString()}</td>
        <td>${listing.square_meters}</td>
        <td>${pricePerSqm}</td>
        <td>${ageHours}h</td>
        <td class="score ${scoreClass}">${listing.score}</td>
        <td><a href="${listing.link}" target="_blank">🔗</a></td>
      `;
      tbody.appendChild(tr);
    });
  });
