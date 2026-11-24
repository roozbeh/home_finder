document.querySelectorAll('.saved-work-detail').forEach(detail => {
  const summary = detail.querySelector('summary');
  let hovering = false;

  detail.addEventListener('pointerenter', event => {
    if (event.pointerType !== 'mouse' || detail.open) return;
    hovering = true;
    detail.open = true;
  });
  detail.addEventListener('pointerleave', () => {
    if (!hovering) return;
    hovering = false;
    detail.open = false;
  });
  summary.addEventListener('click', event => {
    if (!hovering) return;
    event.preventDefault();
    hovering = false; // Keep the preview open when the visitor selects it.
  });
});
