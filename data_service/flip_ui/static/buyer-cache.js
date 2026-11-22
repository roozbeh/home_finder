// Some browsers restore an in-memory view on Back or Forward.
window.addEventListener('pageshow', event => {
  if (event.persisted && (location.pathname === '/' || location.pathname.startsWith('/homes'))) {
    if (!document.querySelector('.receipt')) location.reload();
  }
});
