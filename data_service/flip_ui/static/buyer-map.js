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

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function previewLink(url) {
    const link = element('a', 'buyer-map-preview-link', 'View home');
    link.href = url;
    return link;
  }

  function previewCard(home) {
    const card = element('article', 'buyer-map-preview');
    const photo = element('div', 'buyer-map-preview-photo');
    const unavailable = element('span', '', 'Photo unavailable');
    if (home.photo) {
      const image = document.createElement('img');
      image.alt = home.fictional ? 'Fictional property photo' : 'Property photo';
      image.decoding = 'async';
      image.addEventListener('error', () => photo.replaceChildren(unavailable), { once: true });
      image.src = home.photo;
      photo.append(image);
    } else {
      photo.append(unavailable);
    }

    const copy = element('div', 'buyer-map-preview-copy');
    const price = fullPrice.format(home.price) + (home.market === 'rent' ? ' / mo' : '');
    copy.append(
      element('strong', 'buyer-map-preview-price', price),
      element('h3', '', home.label),
    );
    if (home.locality) copy.append(element('p', 'buyer-map-preview-locality', home.locality));
    const { beds, baths, sqft, property_type: type } = home.facts;
    const facts = [
      Number.isFinite(beds) && `${beds} ${beds === 1 ? 'bed' : 'beds'}`,
      Number.isFinite(baths) && `${baths} ${baths === 1 ? 'bath' : 'baths'}`,
      Number.isFinite(sqft) && `${countFormat.format(sqft)} sq ft`,
      type,
    ].filter(Boolean);
    if (facts.length) copy.append(element('p', '', facts.join(' · ')));
    copy.append(previewLink(home.url));
    card.append(photo, copy);
    return card;
  }

  async function loadPreview(pin, popup, { signal }, focusLink) {
    const endpoint = new URL(pin.url, document.baseURI);
    endpoint.pathname += '/map-preview';
    let content;
    try {
      const response = await fetch(endpoint, { signal });
      if (response.status === 404) {
        content = element('p', 'buyer-map-preview-status', 'This home is no longer available on the map.');
      } else {
        if (!response.ok) throw new Error('Cannot load home preview');
        const home = await response.json();
        if (signal.aborted || !popup.isOpen()) return;
        content = previewCard(home);
      }
    } catch {
      if (signal.aborted) return;
      content = element('div', 'buyer-map-preview-status', 'Home preview unavailable.');
      content.append(previewLink(pin.url));
    }
    if (signal.aborted || !popup.isOpen()) return;
    content.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      popup.remove();
    });
    popup.setContent(content);
    if (focusLink) content.querySelector('a')?.focus({ preventScroll: true });
  }

  function buildMarker(pin) {
    const name = `${pin.label} · ${fullPrice.format(pin.price)}`;
    const marker = L.marker(pin.point, {
      title: name,
      riseOnHover: true,
      icon: L.divIcon({
        className: 'buyer-price-marker', html: `<span>${shortPrice.format(pin.price)}</span>`,
        iconSize: [74, 31], iconAnchor: [37, 15],
      }),
    }).bindPopup('Loading home preview…', {
      className: 'buyer-property-popup',
      maxWidth: 300,
      autoPanPadding: [16, 16],
    });
    let request;
    let openedByKeyboard = false;
    marker.on('add', () => {
      const icon = marker.getElement();
      icon?.setAttribute('aria-label', name);
      icon?.setAttribute('aria-expanded', 'false');
      icon?.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        if (marker.isPopupOpen()) marker.closePopup();
        else {
          openedByKeyboard = true;
          marker.openPopup();
        }
      });
    });
    marker.on('popupopen', ({ popup }) => {
      marker.getElement()?.setAttribute('aria-expanded', 'true');
      popup.setContent(element('p', 'buyer-map-preview-status', 'Loading home preview…'));
      request = new AbortController();
      loadPreview(pin, popup, request, openedByKeyboard);
    });
    marker.on('popupclose', ({ popup }) => {
      request?.abort();
      marker.getElement()?.setAttribute('aria-expanded', 'false');
      popup.setContent('Loading home preview…');
      const focused = document.activeElement;
      if (openedByKeyboard && (focused === document.body || panel.contains(focused))
          && results.dataset.layout !== 'list') marker.getElement()?.focus({ preventScroll: true });
      openedByKeyboard = false;
    });
    return marker;
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
    } else {
      map?.closePopup();
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
