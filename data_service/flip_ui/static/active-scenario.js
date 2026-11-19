const form=document.getElementById('scenario-form'), output=document.getElementById('scenario-output');
const money=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value);
if(form) form.addEventListener('input',()=>{output.hidden=true;document.getElementById('scenario-error').textContent='';});
if(form) form.addEventListener('submit',event=>{
 event.preventDefault(); const v=Object.fromEntries(Array.from(new FormData(form),([key,value])=>[key,Number(value)]));
 if(Object.values(v).some(x=>!Number.isFinite(x)||x<0)||v.repairHigh<v.repairLow||v.months<1||v.resale<=0||v.sellClosing>=100){document.getElementById('scenario-error').textContent='Check the inputs. Renovation high must be at least the low estimate.';output.hidden=true;return;}
 const denominator=1+v.buyClosing/100+(v.interest+v.tax)/100*v.months/12;
 const fixed=v.hoa*v.months+v.other, netSale=v.resale*(1-v.sellClosing/100);
 const ceiling=repair=>(netSale-repair-fixed-v.profit)/denominator;
 const mid=(v.repairLow+v.repairHigh)/2, offer=ceiling(mid), low=ceiling(v.repairHigh), high=ceiling(v.repairLow);
 if(offer<=0||low<=0){document.getElementById('scenario-error').textContent='This scenario does not support a positive purchase price across the renovation range.';output.hidden=true;return;}
 document.getElementById('offer').textContent=money(offer);document.getElementById('offer-range').textContent=money(low)+'–'+money(high)+' across the renovation range';document.getElementById('offer-label').textContent=v.profit===0?'Break-even ceiling · no profit cushion':'Ceiling with '+money(v.profit)+' target profit';
 const lines=[['Proposed purchase',-offer],['Purchase closing',-offer*v.buyClosing/100],['Renovations · midpoint',-mid],['Cost of money',-offer*v.interest/100*v.months/12],['Property tax',-offer*v.tax/100*v.months/12],['HOA carrying cost',-v.hoa*v.months],['Other costs',-v.other],['Sale closing',-v.resale*v.sellClosing/100],['Expected resale',v.resale],['Estimated result',v.profit]];
 const tbody=document.getElementById('cost-lines');tbody.replaceChildren();lines.filter(([label,amount])=>label!=='HOA carrying cost'||amount!==0).forEach(([label,amount])=>{const tr=document.createElement('tr'),th=document.createElement('th'),td=document.createElement('td');th.textContent=label;td.textContent=money(amount);tr.append(th,td);tbody.append(tr);});
 const asking=Number(JSON.parse(document.getElementById('asking-data').textContent));document.getElementById('asking-gap').textContent=offer<asking?money(asking-offer)+' below asking for this scenario.':money(offer-asking)+' above asking for this scenario.'; output.hidden=false;
});
