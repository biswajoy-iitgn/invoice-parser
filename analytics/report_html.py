"""HTML dashboard for one analytics snapshot (charts and data inline, no external scripts).

The page uses the shared application shell (/ui/app.css + app header), so it looks
like every other page when the app serves it. Opened straight from disk the shell
loses its styling, but the charts and data still render.

Chart conventions: one hue for magnitude, fixed categorical order for the stacked view, thin marks,
hairline grid, hover tooltips plus a table twin for every chart.
"""
from __future__ import annotations

import json
from pathlib import Path

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Analytics — Invoice Extractor</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%230052cc'/%3E%3C/svg%3E">
<link rel="stylesheet" href="/ui/app.css">
<style>
/* Report theme, scoped under .ana so its tokens never leak into the app shell. */
.ana{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;
--other:#b9b8b0;--track:#cde2fb;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;
color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.ana .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;min-width:0;margin-bottom:0;box-shadow:none}
.ana .grid{display:grid;gap:16px;margin-bottom:16px}.ana .g2{grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr))}
.ana .kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.ana .kpi .l{color:var(--ink2);font-size:12.5px}.ana .kpi .v{font-size:26px;font-weight:600;letter-spacing:-.01em;margin-top:2px}
.ana .kpi .n{color:var(--muted);font-size:12px;min-height:18px}
.ana h2{font-size:15px;margin:0}.ana .cap{color:var(--ink2);font-size:12.5px;margin:2px 0 10px}
.ana .head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.ana button.tv{font:inherit;font-size:12px;color:var(--ink2);background:none;border:1px solid var(--border);border-radius:7px;padding:3px 9px;cursor:pointer}
.ana button.tv:hover{color:var(--ink)}
.ana svg{display:block;width:100%;height:auto;overflow:visible}.ana svg text{font:11.5px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--muted)}
.ana svg text.lab{fill:var(--ink2)}.ana svg text.val{fill:var(--ink);font-variant-numeric:tabular-nums}
.ana .legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;color:var(--ink2);margin:0 0 8px}
.ana .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.ana .legend i.ln{height:2px;width:14px;vertical-align:3px;border-radius:1px}
.ana table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
.ana th,.ana td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
.ana th{color:var(--ink2);font-weight:500}.ana td.num,.ana th.num{text-align:right}
.ana .tbl{display:none;margin-top:10px;max-height:340px;overflow:auto}.ana .tbl.on{display:block}
.ana ul.say{margin:6px 0 0;padding-left:18px}.ana ul.say li{margin:4px 0}
.ana .sev{white-space:nowrap}.ana .sev i{font-style:normal;margin-right:6px}
.ana .sev.high i{color:var(--critical)}.ana .sev.medium i{color:var(--serious)}.ana .sev.low i{color:var(--muted)}
.ana .inv{color:var(--ink2);font-size:12px}
.ana .meter{display:grid;grid-template-columns:210px 1fr 96px;gap:10px;align-items:center;font-size:12.5px;margin:7px 0}
.ana .meter .t{height:8px;border-radius:99px;background:var(--track);overflow:hidden}.ana .meter .t b{display:block;height:100%;background:var(--s1);border-radius:99px}
.ana .meter span:last-child{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums}
#tip{position:fixed;pointer-events:none;background:#fcfcfb;border:1px solid rgba(11,11,11,.10);border-radius:8px;
box-shadow:0 6px 20px rgba(0,0,0,.14);padding:8px 10px;font-size:12.5px;display:none;z-index:9;max-width:280px;color:#0b0b0b}
#tip .k{color:#52514e}#tip .big{font-weight:600;font-size:14px}
#tip .row{display:flex;align-items:center;gap:6px}#tip .row i{display:inline-block;width:12px;height:2px;border-radius:1px}
.ana .empty{color:var(--muted);font-size:13px;padding:18px 0}
.ana details{margin-top:6px}.ana summary{cursor:pointer;color:var(--ink2);font-size:13px}
.ana .foot{color:var(--muted);font-size:12px;margin-top:18px;word-break:break-all}
@media (max-width:640px){.ana .meter{grid-template-columns:130px 1fr 76px}}
</style></head><body>
<header class="app-header"><a class="skip" href="#main">Skip to content</a>
<a class="brand" href="/classic" aria-label="Invoice Extractor home"><svg class="brand-mark" aria-hidden="true"><use href="/ui/icons.svg#i-logo"/></svg><span class="brand-name">Invoice Extractor</span></a>
<nav class="app-nav" aria-label="Primary"><a href="/classic">Extract</a><a href="/classic/database">Database</a><a href="/analytics" aria-current="page">Analytics</a><a href="/classic/audit">Audit trail</a><a href="/classic/settings">Settings</a></nav></header>
<main class="content" id="main" style="max-width:1180px">
<div class="page-head"><h1>Analytics</h1><p class="page-desc" id="sub"></p></div>
<div class="ana">
<div class="card" style="margin-bottom:16px"><h2>What the numbers say</h2><ul class="say" id="say"></ul></div>
<div class="grid kpis" id="kpis"></div>
<div class="grid"><div class="card" id="c-month"></div></div>
<div class="grid g2"><div class="card" id="c-vendor"></div><div class="card" id="c-cat"></div></div>
<div class="grid"><div class="card" id="c-mix"></div></div>
<div class="grid"><div class="card" id="c-find"></div></div>
<div class="grid g2"><div class="card" id="c-quality"></div><div class="card" id="c-unit"></div></div>
<div class="grid"><div class="card" id="c-review"></div></div>
<div class="card"><h2>How each figure is produced</h2><div class="cap">Every finding lists the invoices behind it; each invoice's values can be traced to the document through the audit trail.</div><div id="methods"></div></div>
<div class="foot" id="foot"></div>
</div></main><div id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const S=JSON.parse(document.getElementById("data").textContent),$=s=>document.querySelector(s),NS="http://www.w3.org/2000/svg";
const inr=v=>{v=+v||0;const a=Math.abs(v);return a>=1e7?"₹"+(v/1e7).toFixed(2)+" Cr":a>=1e5?"₹"+(v/1e5).toFixed(2)+" L":"₹"+Math.round(v).toLocaleString("en-IN")};
const full=v=>"₹"+(+v||0).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
const el=(t,a={},txt)=>{const e=document.createElement(t);for(const k in a)e.setAttribute(k,a[k]);if(txt!=null)e.textContent=txt;return e};
const sv=(t,a={},txt)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(txt!=null)e.textContent=txt;return e};
const tip=$("#tip");
function showTip(ev,rows){tip.replaceChildren();for(const r of rows){const d=el("div",{class:r.cls||"k"});if(r.key){d.className="row";const i=el("i");i.style.background=r.key;d.append(i)}
  const s=el("span",{class:r.cls||"k"},r.text);d.append(s);tip.append(d)}tip.style.display="block";
  const w=tip.offsetWidth,h=tip.offsetHeight;let x=ev.clientX+14,y=ev.clientY+14;if(x+w>innerWidth-8)x=ev.clientX-w-14;if(y+h>innerHeight-8)y=ev.clientY-h-14;tip.style.left=x+"px";tip.style.top=y+"px"}
const hideTip=()=>tip.style.display="none";
function head(card,title,cap,table){const h=el("div",{class:"head"}),l=el("div");l.append(el("h2",{},title),el("div",{class:"cap"},cap));h.append(l);
  if(table){const b=el("button",{class:"tv",type:"button"},"Table");h.append(b);card.append(h);const t=el("div",{class:"tbl"});t.append(table);b.onclick=()=>{t.classList.toggle("on");b.textContent=t.classList.contains("on")?"Hide table":"Table"};return t}
  card.append(h);return null}
function mkTable(cols,rows){const t=el("table"),tr=el("tr");for(const c of cols)tr.append(el("th",c.num?{class:"num"}:{},c.h));const th=el("thead");th.append(tr);t.append(th);const tb=el("tbody");
  for(const r of rows){const x=el("tr");for(const c of cols)x.append(el("td",c.num?{class:"num"}:{},c.f(r)));tb.append(x)}t.append(tb);return t}
function ticks(max,n=4){if(max<=0)return[0];const raw=max/n,p=Math.pow(10,Math.floor(Math.log10(raw))),m=[1,2,2.5,5,10].find(k=>k*p>=raw)*p,out=[];for(let v=0;v<=max+m*.001;v+=m)out.push(v);if(out[out.length-1]<max)out.push(out[out.length-1]+m);return out}
const mon=m=>{const[y,k]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+k-1]+" '"+y.slice(2)};

// ---- header, narrative, KPIs
$("#sub").textContent=`${S.meta.period_from||"—"} to ${S.meta.period_to||"—"} · ${S.meta.invoices.toLocaleString()} invoices · ${S.meta.currency} · generated ${S.meta.generated_at}`;
for(const l of S.narrative)$("#say").append(el("li",{},l));
for(const k of S.kpis){const c=el("div",{class:"card kpi"});c.append(el("div",{class:"l"},k.label),el("div",{class:"v"},k.display),el("div",{class:"n"},k.note||""));$("#kpis").append(c)}

// ---- monthly line (+ forecast)
(function(){const card=$("#c-month"),F=S.forecast.available?S.forecast.points:[],M=S.monthly.filter(m=>!F.length||m.month<F[0].month),cut=S.monthly.length-M.length;
  const tbl=mkTable([{h:"Month",f:r=>r.month},{h:"Invoices",num:1,f:r=>r.invoices==null?"":r.invoices},{h:"Charges",num:1,f:r=>full(r.spend)},{h:"",f:r=>r.low!=null?`forecast, ${inr(r.low)} – ${inr(r.high)}`:""}],M.concat(F));
  const t=head(card,"Charges by month",S.forecast.available?"Before GST. The dashed part is the forecast with its 80% range."+(cut?` ${cut} month(s) from ${F[0].month} are still incomplete and are forecast, not plotted.`:""):"Before GST. "+(S.forecast.reason||""),tbl);
  if(M.length<2){card.append(el("div",{class:"empty"},M.length?"Only one month of invoices so far: "+mon(M[0].month)+", "+inr(M[0].spend)+".":"No dated invoices yet."));card.append(t);return}
  if(F.length){const lg=el("div",{class:"legend"});for(const[n,c,d]of[["Actual","var(--s1)",0],["Forecast","var(--s1)",1]]){const s=el("span"),i=el("i",{class:"ln"});i.style.background=d?"repeating-linear-gradient(90deg,"+c+" 0 4px,transparent 4px 7px)":c;s.append(i,n);lg.append(s)}card.append(lg)}
  const W=760,H=280,L=66,R=74,T=14,B=30,all=M.concat(F),n=all.length,ymax=Math.max(...all.map(d=>d.high??d.spend)),tk=ticks(ymax),top=tk[tk.length-1];
  const X=i=>L+(n===1?0:i*(W-L-R)/(n-1)),Y=v=>T+(1-v/top)*(H-T-B),svg=sv("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"Monthly charges"});
  for(const v of tk){svg.append(sv("line",{x1:L,x2:W-R,y1:Y(v),y2:Y(v),stroke:v?"var(--grid)":"var(--axis)","stroke-width":1}));svg.append(sv("text",{x:L-8,y:Y(v)+4,"text-anchor":"end"},inr(v)))}
  const step=Math.max(1,Math.ceil(n/8));all.forEach((d,i)=>{if(i%step===0||i===n-1&&n-1-Math.floor((n-1)/step)*step>step/2)svg.append(sv("text",{x:X(i),y:H-8,"text-anchor":"middle"},mon(d.month)))});
  const pts=M.map((d,i)=>[X(i),Y(d.spend)]),path=p=>p.map((q,i)=>(i?"L":"M")+q[0].toFixed(1)+" "+q[1].toFixed(1)).join(" ");
  svg.append(sv("path",{d:path(pts)+` L${pts[pts.length-1][0]} ${Y(0)} L${pts[0][0]} ${Y(0)} Z`,fill:"var(--s1)",opacity:.1}));
  if(F.length){const i0=M.length-1,band=[[X(i0),Y(M[i0].spend)]].concat(F.map((d,j)=>[X(i0+1+j),Y(d.high)])).concat(F.slice().reverse().map((d,j)=>[X(n-1-j),Y(d.low)]));
    svg.append(sv("path",{d:path(band)+" Z",fill:"var(--s1)",opacity:.1}));
    svg.append(sv("path",{d:path([[X(i0),Y(M[i0].spend)]].concat(F.map((d,j)=>[X(i0+1+j),Y(d.spend)]))),fill:"none",stroke:"var(--s1)","stroke-width":2,"stroke-dasharray":"5 4","stroke-linecap":"round"}))}
  svg.append(sv("path",{d:path(pts),fill:"none",stroke:"var(--s1)","stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"}));
  const lp=pts[pts.length-1];svg.append(sv("circle",{cx:lp[0],cy:lp[1],r:4,fill:"var(--s1)",stroke:"var(--surface)","stroke-width":2}));
  if(!F.length)svg.append(sv("text",{x:lp[0]+8,y:lp[1]+4,class:"val"},inr(M[M.length-1].spend)));
  else{const f=F[F.length-1];svg.append(sv("text",{x:X(n-1)+8,y:Y(f.spend)+4,class:"val"},inr(f.spend)))}
  const cross=sv("line",{y1:T,y2:H-B,stroke:"var(--axis)","stroke-width":1,visibility:"hidden"}),dot=sv("circle",{r:4,fill:"var(--s1)",stroke:"var(--surface)","stroke-width":2,visibility:"hidden"});svg.append(cross,dot);
  const hit=sv("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"});svg.append(hit);
  hit.setAttribute("tabindex","0");
  hit.addEventListener("focus",()=>{const d=all[n-1],r=hit.getBoundingClientRect();showTip({clientX:r.left+r.width/2,clientY:r.top},[{text:inr(d.spend),cls:"big"},{text:mon(d.month)+(d.low!=null?" · forecast":"")}])});
  hit.addEventListener("blur",hideTip);
  hit.addEventListener("pointermove",ev=>{const r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)*W/r.width,i=Math.max(0,Math.min(n-1,Math.round((x-L)/((W-L-R)/Math.max(1,n-1))))),d=all[i];
    cross.setAttribute("x1",X(i));cross.setAttribute("x2",X(i));cross.setAttribute("visibility","visible");dot.setAttribute("cx",X(i));dot.setAttribute("cy",Y(d.spend));dot.setAttribute("visibility","visible");
    showTip(ev,[{text:inr(d.spend),cls:"big"},{text:mon(d.month)+(d.low!=null?" · forecast":"")},d.low!=null?{text:"likely between "+inr(d.low)+" and "+inr(d.high)}:{text:d.invoices+" invoices"}])});
  hit.addEventListener("pointerleave",()=>{hideTip();cross.setAttribute("visibility","hidden");dot.setAttribute("visibility","hidden")});
  card.append(svg,t)})();

// ---- horizontal bars (one hue: magnitude)
function hbars(card,title,cap,rows,name,extra,cols){const t=head(card,title,cap,mkTable(cols,rows));
  if(!rows.length){card.append(el("div",{class:"empty"},"Nothing to show yet."));return}
  const top=rows.slice(0,10),W=560,L=190,R=78,rh=30,H=top.length*rh+8,max=Math.max(...top.map(r=>r.spend))||1,svg=sv("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":title});
  svg.append(sv("line",{x1:L,x2:L,y1:2,y2:H-4,stroke:"var(--axis)","stroke-width":1}));
  top.forEach((r,i)=>{const y=6+i*rh,w=Math.max(2,(W-L-R)*r.spend/max),label=r[name].length>27?r[name].slice(0,26)+"…":r[name];
    const tx=sv("text",{x:L-8,y:y+13,"text-anchor":"end",class:"lab"},label);tx.append(sv("title",{},r[name]));svg.append(tx);
    const rr=Math.min(4,w),bar=sv("path",{d:`M${L} ${y}h${w-rr}a${rr} ${rr} 0 0 1 ${rr} ${rr}v${16-2*rr}a${rr} ${rr} 0 0 1 -${rr} ${rr}h-${w-rr}z`,fill:"var(--s1)",tabindex:0});
    svg.append(bar,sv("text",{x:L+w+7,y:y+13,class:"val"},inr(r.spend)));
    const hit=sv("rect",{x:0,y:y-6,width:W,height:rh,fill:"transparent"});svg.append(hit);
    const on=ev=>{bar.setAttribute("opacity",.78);showTip(ev,[{text:full(r.spend),cls:"big"},{text:r[name]},{text:extra(r)}])},off=()=>{bar.setAttribute("opacity",1);hideTip()};
    const kb=()=>{const b=bar.getBoundingClientRect();on({clientX:b.left+b.width/2,clientY:b.top})};
    hit.addEventListener("pointermove",on);hit.addEventListener("pointerleave",off);
    bar.addEventListener("focus",kb);bar.addEventListener("blur",off)});
  card.append(svg);if(rows.length>10)card.append(el("div",{class:"cap"},`Largest 10 of ${rows.length}; the table has all of them.`));card.append(t)}
hbars($("#c-vendor"),"Charges by vendor","Before GST, largest first.",S.vendors,"vendor",r=>`${r.share}% of spend · ${r.invoices} invoices`,
  [{h:"Vendor",f:r=>r.vendor},{h:"Invoices",num:1,f:r=>r.invoices},{h:"Charges",num:1,f:r=>full(r.spend)},{h:"Share",num:1,f:r=>r.share+"%"}]);
hbars($("#c-cat"),"Charges by category","Before GST, largest first.",S.categories,"category",r=>`${r.share}% of spend · on ${r.invoices} invoices`,
  [{h:"Category",f:r=>r.category},{h:"Invoices",num:1,f:r=>r.invoices},{h:"Total",num:1,f:r=>full(r.spend)},{h:"Share",num:1,f:r=>r.share+"%"}]);

// ---- vendor x category (part-to-whole, fixed categorical order, 2px surface gaps)
(function(){const card=$("#c-mix"),V=S.vendor_category,C=["var(--s1)","var(--s2)","var(--s3)","var(--s4)","var(--s5)","var(--s6)","var(--other)"];
  const color=i=>V.categories[i]==="Other categories"?"var(--other)":C[i];
  const t=head(card,"Where each vendor's charges go","Share of each vendor's own charges by category (every bar is 100% of that vendor); the vendor's total is on the right.",
    mkTable([{h:"Vendor",f:r=>r.vendor}].concat(V.categories.map((c,i)=>({h:c,num:1,f:r=>full(r.values[i])}))),V.rows));
  if(!V.rows.length){card.append(el("div",{class:"empty"},"Nothing to show yet."),t);return}
  const lg=el("div",{class:"legend"});V.categories.forEach((c,i)=>{const s=el("span"),sw=el("i");sw.style.background=color(i);s.append(sw,c);lg.append(s)});card.append(lg);
  const W=1100,L=230,R=90,rh=30,H=V.rows.length*rh+8,svg=sv("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"Charges by vendor and category"});
  svg.append(sv("line",{x1:L,x2:L,y1:2,y2:H-4,stroke:"var(--axis)","stroke-width":1}));
  V.rows.forEach((r,j)=>{const y=6+j*rh,tot=r.values.reduce((a,b)=>a+b,0),label=r.vendor.length>32?r.vendor.slice(0,31)+"…":r.vendor;
    const tx=sv("text",{x:L-8,y:y+13,"text-anchor":"end",class:"lab"},label);tx.append(sv("title",{},r.vendor));svg.append(tx);let x=L;
    r.values.forEach((v,i)=>{if(v<=0||tot<=0)return;const w=(W-L-R)*v/tot,seg=sv("rect",{x:x+1,y:y,width:Math.max(1,w-2),height:16,fill:color(i),rx:1,tabindex:0});svg.append(seg);
      const on=ev=>{seg.setAttribute("opacity",.78);showTip(ev,[{text:full(v),cls:"big"},{text:V.categories[i],key:color(i)},{text:`${(100*v/tot).toFixed(0)}% of ${r.vendor}`}])},off=()=>{seg.setAttribute("opacity",1);hideTip()};
      const kb=()=>{const b=seg.getBoundingClientRect();on({clientX:b.left+b.width/2,clientY:b.top})};
      seg.addEventListener("pointermove",on);seg.addEventListener("pointerleave",off);
      seg.addEventListener("focus",kb);seg.addEventListener("blur",off);x+=w});
    svg.append(sv("text",{x:x+7,y:y+13,class:"val"},inr(tot)))});
  card.append(svg,t)})();

// ---- findings
(function(){const card=$("#c-find"),F=S.findings,icon={high:"▲",medium:"●",low:"○"},name={high:"High",medium:"Medium",low:"Low"};
  head(card,"Findings to check",F.length?`${F.length} in total, most urgent first. Each one names the invoices it is based on.`:"Nothing was flagged in this period.");
  if(!F.length){card.append(el("div",{class:"empty"},S.meta.invoices<40?"Outlier and anomaly checks need more invoices to compare against (see the method notes below).":"No issues found."));return}
  const t=el("table"),tb=el("tbody"),hr=el("tr");for(const h of["Priority","Finding","Invoices"])hr.append(el("th",{},h));const th=el("thead");th.append(hr);t.append(th);
  for(const f of F.slice(0,60)){const r=el("tr"),s=el("td",{class:"sev "+f.severity});s.append(el("i",{},icon[f.severity]),name[f.severity]);const d=el("td");d.append(el("div",{},f.title),el("div",{class:"inv"},f.detail));
    r.append(s,d,el("td",{class:"inv"},f.invoices.join(", ")));tb.append(r)}t.append(tb);const w=el("div",{style:"max-height:520px;overflow:auto"});w.append(t);card.append(w);
  if(F.length>60)card.append(el("div",{class:"cap"},`Showing 60 of ${F.length}; the MIS workbook lists all of them.`))})();

// ---- data quality (meters) and freight per kg
(function(){const card=$("#c-quality"),Q=S.quality||{};head(card,"Data completeness","Share of invoices where each field was captured.");
  for(const r of Q.fill_rates||[]){const m=el("div",{class:"meter"}),tr=el("div",{class:"t"}),b=el("b");b.style.width=r.rate+"%";tr.append(b);m.append(el("span",{},r.field),tr,el("span",{},`${r.rate}% (${r.filled}/${r.of})`));card.append(m)}
  const parts=[];if(Q.review_status)parts.push("Review: "+Object.entries(Q.review_status).map(([k,v])=>`${k.toLowerCase()} ${v}`).join(", "));
  if(Q.runs&&Q.runs.routes)parts.push("Latest run routed: "+Object.entries(Q.runs.routes).map(([k,v])=>`${k.replace("_"," ").toLowerCase()} ${v}`).join(", "));
  if(Q.runs)parts.push(`${Q.runs.invoices_with_warnings} invoices carry at least one warning`);
  for(const p of parts)card.append(el("div",{class:"cap"},p))})();
(function(){const card=$("#c-unit"),U=S.unit_costs;head(card,"Freight per kilogram","Median freight ÷ weight per vendor (3+ invoices with both). Lowest first within each mode.");
  if(!U.length){card.append(el("div",{class:"empty"},"Needs at least three invoices per vendor with both a freight amount and a weight."));return}
  const w=el("div",{style:"max-height:330px;overflow:auto"});w.append(mkTable([{h:"Vendor",f:r=>r.vendor},{h:"Mode",f:r=>r.mode},{h:"Invoices",num:1,f:r=>r.invoices},{h:"Median ₹/kg",num:1,f:r=>r.median_per_kg.toFixed(2)},{h:"Range",num:1,f:r=>r.min_per_kg.toFixed(2)+" – "+r.max_per_kg.toFixed(2)}],U));card.append(w)})();
(function(){const card=$("#c-review"),R=S.review_queue;head(card,"Waiting for review",R.length?`${R.length}${R.length>=200?"+":""} invoices are not yet approved.`:"No invoices are waiting.");
  if(R.length){const w=el("div",{style:"max-height:300px;overflow:auto"});w.append(mkTable([{h:"Invoice",f:r=>r.invoice},{h:"Vendor",f:r=>r.vendor},{h:"Date",f:r=>r.date},{h:"Charges",num:1,f:r=>full(r.total)}],R));card.append(w)}})();
for(const[k,v]of Object.entries(S.methods)){const d=el("details"),s=el("summary",{},k.replace(/_/g," "));d.append(s,el("div",{class:"cap"},v));$("#methods").append(d)}
$("#foot").textContent=`Engine ${S.meta.engine} · source ${(S.meta.source||{}).view||""} on ${(S.meta.source||{}).host||""} · input fingerprint ${S.meta.fingerprint||"—"}`;
</script></body></html>"""


def build(snap: dict, out: Path) -> Path:
    data = json.dumps(snap, default=str).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    return out
