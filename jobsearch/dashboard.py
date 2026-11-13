"""Generowanie pojedynczego lokalnego dashboardu HTML."""
from __future__ import annotations
import html
import json
from pathlib import Path
from .core import OFFER_KEYS
from .sources import clean_url

def _safe_json(value: object) -> str:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))

def render_dashboard(offers: list[dict[str, str]], updated: str) -> str:
    public = []
    for offer in offers:
        row = {key: offer.get(key, "") for key in OFFER_KEYS}
        row["url"] = clean_url(row["url"], allow_http=row["source"] == "adzuna") or ""
        public.append(row)
    encoded = _safe_json(public)
    safe_updated = html.escape(updated, quote=True)
    return """<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Oferty pracy</title>
<style>
:root{color-scheme:light;--ink:#18212b;--muted:#607080;--line:#dbe2e8;--accent:#155eef}
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1050px;margin:auto;padding:32px 20px}h1{margin:0;font-size:2rem}.muted{color:var(--muted)}
.controls{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.controls input,.controls select{padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:white;font:inherit}
#query{flex:1;min-width:240px}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:18px;margin:12px 0}
.card h2{font-size:1.1rem;margin:0 0 5px}.card p{margin:4px 0}.card a{display:inline-flex;align-items:center;justify-content:center;margin:10px 12px 0 0;color:var(--accent);font-weight:600}
.adzuna-credit{min-width:116px;min-height:23px}
#results{margin-top:18px}.empty{padding:30px;text-align:center;color:var(--muted)}
@media(max-width:600px){main{padding:22px 14px}.controls>*{width:100%}}
</style></head><body><main><header><h1>Oferty pracy</h1>
<p class="muted">Dane z Twoich skonfigurowanych publicznych źródeł. Aktualizacja: <time>""" + safe_updated + """</time></p>
</header><section class="controls" aria-label="Filtry">
<input id="query" type="search" placeholder="Szukaj stanowiska, firmy lub miejsca" aria-label="Szukaj">
<select id="source" aria-label="Filtruj według źródła"><option value="">Wszystkie źródła</option></select>
<select id="sort" aria-label="Sortuj"><option value="new">Najnowsze</option><option value="old">Najstarsze</option></select>
</section><p id="count" class="muted" aria-live="polite"></p><section id="results"></section></main>
<script type="application/json" id="offers">""" + encoded + """</script>
<script>
"use strict";
const offers=JSON.parse(document.getElementById("offers").textContent);
const source=document.getElementById("source"), query=document.getElementById("query"), sort=document.getElementById("sort");
const results=document.getElementById("results"), count=document.getElementById("count");
const sources=[...new Set(offers.map(x=>x.source))].sort();
for(const name of sources){const option=document.createElement("option");option.value=name;option.textContent=name;source.append(option)}
function element(tag,text,className){const node=document.createElement(tag);node.textContent=text||"";if(className)node.className=className;return node}
function safeLink(value){
 try{const url=new URL(value);if((url.protocol==="https:"||url.protocol==="http:")&&!url.username&&!url.password)return url.href}catch{}
 return ""
}
function render(){
 const term=query.value.trim().toLocaleLowerCase("pl");
 let rows=offers.filter(x=>(!source.value||x.source===source.value)&&
   (!term||[x.title,x.company,x.location].join(" ").toLocaleLowerCase("pl").includes(term)));
 rows.sort((a,b)=>(sort.value==="new"?b.first_seen.localeCompare(a.first_seen):a.first_seen.localeCompare(b.first_seen)));
 results.replaceChildren();count.textContent="Liczba ofert: "+rows.length;
 if(!rows.length){results.append(element("p","Nie znaleziono ofert dla tych filtrów.","empty"));return}
 for(const offer of rows){
   const card=document.createElement("article");card.className="card";
   card.append(element("h2",offer.title),element("p",offer.company,"muted"),element("p",offer.location));
   const meta=element("p","Źródło: "+offer.source+" · dodano: "+offer.first_seen.slice(0,10),"muted");card.append(meta);
   const offerUrl=safeLink(offer.url);
   if(offerUrl){const link=element("a","Otwórz ofertę");link.href=offerUrl;link.rel="noopener noreferrer";link.target="_blank";card.append(link)}
   if(offer.source==="adzuna"){
     const credit=element("a","Adzuna","adzuna-credit");credit.href="https://www.adzuna.pl/";credit.rel="noopener noreferrer";credit.target="_blank";
     credit.setAttribute("aria-label","Adzuna — źródło ofert pracy");card.append(credit)
   }
   results.append(card)
 }
}
query.addEventListener("input",render);source.addEventListener("change",render);sort.addEventListener("change",render);render();
</script></body></html>"""

def write_dashboard(path: Path, offers: list[dict[str, str]], updated: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(offers, updated), encoding="utf-8")
