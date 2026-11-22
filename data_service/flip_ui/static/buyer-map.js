(() => {
  const results = document.getElementById('buyer-results');
  const controls = document.getElementById('buyer-view-switch');
  const panel = document.getElementById('buyer-map');
  const message = document.getElementById('buyer-map-message');
  const alert = document.getElementById('buyer-map-alert');
  const mobile = matchMedia('(max-width: 800px)');
  const layouts = [...controls.querySelectorAll('button')];
  const fullPrice = new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  });
  const shortPrice = new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1,
  });
  const countFormat = new Intl.NumberFormat('en-US');
  let map;
  let mapData;

  function fallback(text) {
    const mapHadFocus = panel.contains(document.activeElement) || controls.contains(document.activeElement);
    setLayout('list');
    controls.hidden = true;
    if (text) alert.textContent = text;
    alert.hidden = false;
    if (mapHadFocus) results.querySelector('.buyer-list a')?.focus();
  }

  function buildMarker(pin) {
    const name = `${pin.label} · ${fullPrice.format(pin.price)}`;
    return L.marker(pin.point, {
      title: name,
      riseOnHover: true,
      icon: L.divIcon({
        className: 'buyer-price-marker', html: `<span>${shortPrice.format(pin.price)}</span>`,
        iconSize: [74, 31], iconAnchor: [37, 15],
      }),
    }).bindPopup(() => {
      const popup = document.createElement('div');
      popup.className = 'buyer-map-popup';
      const link = document.createElement('a');
      link.href = pin.url;
      link.textContent = name;
      popup.append(link);
      return popup;
    });
  }

  function createMap(pins) {
    map = L.map('buyer-street-map', { scrollWheelZoom: false, maxZoom: 19 });
    map.fitBounds(pins.map(pin => pin.point), { padding: [44, 40], maxZoom: 14 });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      referrerPolicy: 'strict-origin',
    });
    let tileLoaded = false;
    tiles.on('loading', () => { tileLoaded = false; });
    tiles.on('tileload', () => { tileLoaded = true; });
    tiles.on('load', () => { if (!tileLoaded) fallback(); });
    tiles.addTo(map);

    const clusters = L.markerClusterGroup({
      animate: !matchMedia('(prefers-reduced-motion: reduce)').matches,
      chunkedLoading: true,
      chunkInterval: 50,
      chunkDelay: 20,
      maxClusterRadius: 62,
      showCoverageOnHover: false,
      iconCreateFunction(cluster) {
        const count = countFormat.format(cluster.getChildCount());
        const diameter = cluster.getChildCount() >= 100 ? 52 : 43;
        cluster.options.title = `${count} homes`;
        return L.divIcon({
          className: 'buyer-cluster-marker', html: `<span>${count}</span>`,
          iconSize: [diameter, diameter],
        });
      },
      chunkProgress(processed, total) {
        if (processed === total) {
          const label = total === 1 ? 'home' : 'homes';
          message.textContent = `${countFormat.format(total)} ${label} with a published location. Select a group to zoom in or a price to see the home.`;
        }
      },
    }).addTo(map);
    clusters.addLayers(pins.map(buildMarker));
  }

  async function showMap() {
    try {
      if (!mapData) {
        mapData = fetch(panel.dataset.mapUrl).then(response => {
          if (!response.ok) throw new Error('Cannot load homes on the map');
          return response.json();
        });
      }
      const pins = await mapData;
      if (map || results.dataset.layout === 'list') return;
      if (!pins.length) {
        fallback('No homes in these results have a published map location.');
        return;
      }
      createMap(pins);
    } catch {
      fallback();
    }
  }

  function setLayout(layout) {
    results.dataset.layout = layout;
    for (const button of layouts) {
      button.setAttribute('aria-pressed', String(button.dataset.layout === layout));
    }
    if (layout !== 'list') {
      if (map) map.invalidateSize();
      else showMap();
    }
  }

  if (!window.L?.markerClusterGroup) {
    fallback();
    return;
  }
  controls.hidden = false;
  controls.addEventListener('click', event => {
    const button = event.target.closest('button[data-layout]');
    if (button) setLayout(button.dataset.layout);
  });
  mobile.addEventListener('change', event => {
    if (event.matches && results.dataset.layout === 'split') setLayout('list');
  });
  setLayout(mobile.matches ? 'list' : 'split');
})();
