(() => {
  const modal=document.getElementById('property-photo-modal');
  if (!modal) return;
  const photos=JSON.parse(document.getElementById('property-photo-data').textContent);
  const image=modal.querySelector('.photo-modal-image');
  const previous=modal.querySelector('.photo-modal-previous');
  const next=modal.querySelector('.photo-modal-next');
  const close=modal.querySelector('.photo-modal-close');
  const counter=modal.querySelector('.photo-modal-counter');
  const error=modal.querySelector('.photo-modal-error');
  let current=0, opener=null;
  function show(index) {
    if (!photos.length) return;
    current=Math.max(0,Math.min(index,photos.length-1));
    error.hidden=true;image.hidden=false;
    image.alt='Property photo '+(current+1)+' of '+photos.length;
    image.src=photos[current];
    counter.textContent=(current+1)+' / '+photos.length;
    previous.disabled=current===0;next.disabled=current===photos.length-1;
  }
  image.addEventListener('error',()=>{image.hidden=true;error.hidden=false;});
  image.addEventListener('load',()=>{image.hidden=false;error.hidden=true;});
  document.querySelectorAll('.hero-photo-open,.photo-select,.room-photo-open').forEach(button=>{
    button.addEventListener('click',()=>{
      const index=photos.indexOf(button.dataset.photo);
      if (index<0) return;
      opener=button;show(index);modal.showModal();
      document.body.classList.add('photo-modal-open');close.focus();
    });
  });
  previous.addEventListener('click',()=>show(current-1));
  next.addEventListener('click',()=>show(current+1));
  close.addEventListener('click',()=>modal.close());
  modal.addEventListener('keydown',event=>{
    if(event.key==='ArrowLeft'){event.preventDefault();show(current-1);}
    if(event.key==='ArrowRight'){event.preventDefault();show(current+1);}
  });
  modal.addEventListener('click',event=>{
    if(event.target!==modal)return;
    const bounds=modal.getBoundingClientRect();
    if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)modal.close();
  });
  modal.addEventListener('close',()=>{
    document.body.classList.remove('photo-modal-open');
    if(opener)opener.focus();
  });
})();
