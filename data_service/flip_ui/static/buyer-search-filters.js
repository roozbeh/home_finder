(() => {
  const form = document.querySelector('.buyer-search-controls');
  if (!form) return;
  const filters = [...form.querySelectorAll('.search-filter')];

  filters.forEach(filter => {
    filter.addEventListener('toggle', () => {
      if (filter.open) filters.forEach(other => { if (other !== filter) other.open = false; });
    });
    filter.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !filter.open) return;
      filter.open = false;
      filter.querySelector('summary').focus();
    });
  });
  document.addEventListener('click', event => {
    filters.forEach(filter => { if (!filter.contains(event.target)) filter.open = false; });
  });

  const typeFilter = form.querySelector('.search-type-filter');
  if (!typeFilter) return;
  const any = typeFilter.querySelector('.search-type-any');
  const types = [...typeFilter.querySelectorAll('input[name=property_type]')];
  const apply = typeFilter.querySelector('.search-type-apply');
  const numberFormat = new Intl.NumberFormat(document.documentElement.lang || 'en');
  let timer, pending;

  function cancelCount() {
    clearTimeout(timer);
    pending?.abort();
  }

  function updateTypes() {
    any.setAttribute('aria-pressed', String(!types.some(type => type.checked)));
    cancelCount();
    if (!typeFilter.open) return;
    apply.textContent = 'See results';
    if (!form.checkValidity()) return;
    timer = setTimeout(async () => {
      const url = new URL(typeFilter.dataset.countUrl, document.baseURI);
      url.search = new URLSearchParams(new FormData(form));
      const controller = new AbortController();
      pending = controller;
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) return;
        const { count } = await response.json();
        if (controller.signal.aborted || !Number.isInteger(count)) return;
        apply.textContent = `See ${numberFormat.format(count)} ${count === 1 ? 'result' : 'results'}`;
      } catch {
        // The standard submit remains available if the count request is interrupted.
      }
    }, 120);
  }

  typeFilter.addEventListener('toggle', updateTypes);
  form.addEventListener('input', updateTypes);
  any.addEventListener('click', event => {
    event.preventDefault();
    types.forEach(type => { type.checked = false; });
    updateTypes();
  });
})();
