const rows = JSON.parse(document.getElementById('active-data').textContent);
const results = document.querySelector('.active-results');
let map;
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  results.dataset.layout = button.dataset.view;
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  if (map) setTimeout(() => map.invalidateSize(), 50);
}));
if (typeof L !== 'undefined') {
  map = L.map('active-map').setView([37.78,-122.15],9);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  const points=[];
  rows.forEach(row => {
    if (!Number.isFinite(row.lat)||!Number.isFinite(row.lng)) return;
    points.push([row.lat,row.lng]);
    const marker=L.marker([row.lat,row.lng],{icon:L.divIcon({className:'price-pin',html:'<span>$'+Math.round(row.price/1000)+'k</span>',iconSize:[76,30],iconAnchor:[38,30]})}).addTo(map);
    const popup=document.createElement('div');
    const link=document.createElement('a');link.href='/admin/active/'+encodeURIComponent(row.id);link.textContent=row.address+' · '+new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(row.price);popup.append(link);
    marker.bindPopup(popup);
    marker.on('click',()=>{document.querySelectorAll('.active-card').forEach(c=>c.classList.remove('highlighted'));const card=document.getElementById('listing-'+row.id);card.classList.add('highlighted');card.scrollIntoView({behavior:'smooth',block:'nearest'});});
    const card=document.getElementById('listing-'+row.id);card.addEventListener('mouseenter',()=>marker.openPopup());card.addEventListener('focus',()=>marker.openPopup());
  });
  if(points.length)map.fitBounds(points,{padding:[35,35],maxZoom:14});
  document.getElementById('map-message').textContent='Pins show the current results page. Select a price, then the address to open the property.';
} else document.getElementById('map-message').textContent='Map could not load. Open a property from the list.';
