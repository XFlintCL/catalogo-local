#!/usr/bin/env python3
"""Genera catalogo/index.html (vista tipo Netflix) a partir de catalogo/catalogo.db.
Uso: python generar_web.py [carpeta]   (por defecto: catalogo)"""
import json, re, sqlite3, sys, unicodedata
from collections import Counter
from pathlib import Path

EN_ES = {"action": "Acción", "crime": "Crimen", "animation": "Animación", "adventure": "Aventuras",
         "sci-fi": "Ciencia ficción", "science fiction": "Ciencia ficción", "family": "Familia",
         "fantasy": "Fantástico", "comedy": "Comedia", "mystery": "Intriga", "history": "Histórico",
         "horror": "Terror", "documentary": "Documental", "war": "Bélico", "biography": "Biográfico",
         "sport": "Deporte", "music": "Música", "musical": "Musical", "romance": "Romance",
         "drama": "Drama", "thriller": "Thriller", "western": "Western"}
NO_GENERO = {"serie de tv", "secuela", "precuela", "remake", "reboot", "spin-off", "3-d",
             "película de culto", "telefilm"}

def generos(txt):
    out = []
    for g in (txt or "").split(","):
        g = g.strip()
        if not g or g.lower() in NO_GENERO:
            continue
        g = EN_ES.get(g.lower(), g)
        if g not in out:
            out.append(g)
    return out

def es_latino(s):
    return bool(s) and all(unicodedata.name(c, "").startswith("LATIN") for c in s if c.isalpha())

def mostrar(orig, trad):
    """Título para mostrar y ordenar: el original si usa alfabeto latino; si no, el traducido."""
    o, t = (orig or "").strip(), (trad or "").strip()
    return o if es_latino(o) else (t if es_latino(t) else (o or t))

def alternativo(orig, trad):
    m, o, t = mostrar(orig, trad), (orig or "").strip(), (trad or "").strip()
    alt = t if m == o else o
    return alt if alt and alt != m else None

def clave_orden(s):
    t = re.sub(r"[\u0300-\u036f]", "", unicodedata.normalize("NFD", s)).lower()
    return re.sub(r"^[^a-z0-9]+", "", t) or t      # ignora comillas o signos al inicio

def temporadas(t):
    if not t: return None
    t = t.strip()
    m = re.fullmatch(r"(?:S|Season\s*)(\d+)(?:\s*-\s*S?(\d+))?\s*(compl?et[ae])?", t, re.I)
    if m:
        a, b = int(m[1]), int(m[2]) if m[2] else None
        txt = f"Temporada {a}" if not b or b == a else f"Temporadas {a} a {b}"
        return txt + (" · completa" if m[3] else "")
    if re.search(r"compl?et[ae]", t, re.I): return "Serie completa"
    if t.lower() == "miniserie": return "Miniserie"
    return t


PLANTILLA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mi catálogo</title>
<style>
:root{--bg:#141414;--card:#1f1f1f;--tx:#f2f2f2;--mu:#9a9a9a;--ac:#e50914}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:14px 4vw 10px;border-bottom:1px solid #2a2a2a}
.top{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
h1{margin:0;font-size:22px;color:var(--ac);letter-spacing:1px}
#q{flex:1;min-width:200px;max-width:420px;padding:9px 14px;border-radius:6px;border:1px solid #444;background:#222;color:var(--tx);font-size:15px}
.seg button,.chips button{border:1px solid #444;background:transparent;color:var(--mu);padding:6px 13px;border-radius:20px;cursor:pointer;font-size:13px}
.seg button.on,.chips button.on{background:var(--tx);color:#111;border-color:var(--tx)}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px;align-items:center}
select{background:#222;color:var(--mu);border:1px solid #444;border-radius:20px;padding:6px 10px;font-size:13px}
#n{color:var(--mu);font-size:13px;margin-left:auto}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:22px 16px;padding:20px 4vw 50px}
.c{transition:transform .15s}.c:hover{transform:scale(1.04)}
.im{position:relative;aspect-ratio:2/3;background:#2a2a2a;border-radius:6px;overflow:hidden}
.im img{width:100%;height:100%;object-fit:cover;display:block}
.b{position:absolute;top:7px;left:7px;font-size:11px;font-weight:700;padding:2px 7px;border-radius:3px;background:#000b;text-transform:uppercase}
.b.serie{background:var(--ac)}
h3{font-size:14px;margin:8px 0 2px;line-height:1.25}
p{margin:2px 0;font-size:12px;color:var(--mu)}.g{color:#cfcfcf}.s{color:#7fd48a}
.ov{position:absolute;inset:0;background:#000d;padding:12px;font-size:12px;line-height:1.4;color:#ddd;opacity:0;transition:opacity .2s;display:-webkit-box;-webkit-line-clamp:12;-webkit-box-orient:vertical;overflow:hidden}
.c{cursor:pointer}.c:hover .ov{opacity:1}.f{color:#6ab0ff}
dialog{background:var(--card);color:var(--tx);border:0;border-radius:10px;max-width:860px;width:92vw;max-height:88vh;padding:22px;overflow:auto}
dialog::backdrop{background:#000b}.x{position:absolute;top:10px;right:14px;background:none;border:0;color:var(--tx);font-size:22px;cursor:pointer}
.fi{display:flex;gap:20px;flex-wrap:wrap}.fi>img{width:220px;max-width:100%;border-radius:6px;align-self:flex-start}.fi>div{flex:1;min-width:260px}
.fi h2{margin:0 0 4px}.fi p{font-size:14px}.sin{white-space:pre-line;color:#ddd;line-height:1.5}.fi b{color:var(--tx)}a{color:#6ab0ff}
a.add{color:#fff;background:var(--ac);padding:6px 13px;border-radius:20px;font-size:13px;text-decoration:none}
.ef{display:block;margin:10px 0;font-size:13px;color:#9a9a9a}.ef input,.ef textarea,.ef select{display:block;width:100%;margin-top:4px;background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px;font:inherit}
.ed{position:absolute;top:10px;right:52px;background:#333;color:#fff;border:0;border-radius:6px;padding:5px 10px;cursor:pointer}
.abc{display:flex;gap:4px;flex-wrap:wrap;margin-top:10px}.abc button{border:1px solid #444;background:transparent;color:var(--mu);padding:3px 9px;border-radius:6px;cursor:pointer;font-size:12px}.abc button.on{background:var(--tx);color:#111}
.tb{background:#333;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}.tb.on{background:var(--ac)}
#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}@keyframes barra{0%{left:-35%}100%{left:100%}}
.rail{display:flex;gap:10px;overflow-x:auto;padding:6px 0}.mini{flex:0 0 96px;cursor:pointer;color:inherit;text-decoration:none;font-size:11px}.mini img,.mini .ph{width:96px;height:144px;object-fit:cover;border-radius:5px;background:#2a2a2a;display:block}.mini span{display:block;margin-top:4px;line-height:1.2}.mini small{color:var(--mu)}.mini small.si{color:#7fd48a}.fi h4{margin:18px 0 2px}
main.home{display:block}.rowc h2{font-size:19px;margin:24px 0 8px}.rowc h2 a{font-size:12px;font-weight:400;margin-left:10px}.rail.big{gap:14px;padding:8px 2px 14px}.rail.big .c{flex:0 0 150px}.mas{grid-column:1/-1;text-align:center;padding:10px}.c.off .im img{filter:grayscale(.5) brightness(.8)}.c.off h3{opacity:.75}
.stars{margin-left:10px;white-space:nowrap}.star{cursor:pointer;font-size:24px;color:#555;line-height:1}.star.on{color:#f5c26b}.star:hover{color:#ffd27d}.c .st5{color:#f5c26b;letter-spacing:1px}
#bar{position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:50;background:#000e;border:1px solid #444;border-radius:14px;padding:10px 14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;max-width:96vw;box-shadow:0 6px 30px #000a}#bar[hidden]{display:none}
.c.sel .im{outline:4px solid var(--ac);outline-offset:-4px}.c.sel .im::after{content:'✓';position:absolute;top:6px;right:8px;background:var(--ac);color:#fff;width:26px;height:26px;border-radius:50%;text-align:center;line-height:26px;font-weight:700}body.selm main{padding-bottom:120px}body.selm .c{cursor:copy}
.menu{position:relative}.menu summary{cursor:pointer;list-style:none;background:#333;color:#fff;border-radius:6px;padding:6px 12px;font-size:13px}.menu summary::-webkit-details-marker{display:none}
.menu .mp{position:absolute;right:0;top:36px;background:#1c1c1c;border:1px solid #444;border-radius:10px;padding:8px 0;min-width:230px;z-index:60;box-shadow:0 8px 30px #000a}
.menu b{display:block;padding:8px 16px 2px;font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.5px}.menu a{display:block;padding:7px 16px;color:#eee;text-decoration:none;font-size:14px}.menu a:hover{background:#2a2a2a}
#logo:hover{opacity:.8}.foot{color:#666;font-size:11px;text-align:center;padding:20px}
.hero{position:relative;height:clamp(260px,32vw,440px);border-radius:10px;overflow:hidden;margin-bottom:6px;background:#000}.hs{position:absolute;inset:0;background:linear-gradient(135deg,#2a2a2a,#111) center/cover;background-position:center 20%;opacity:0;transition:opacity .8s}.hs.on{opacity:1}
.hs::before{content:'';position:absolute;inset:0;background:linear-gradient(90deg,#000e 0%,#000a 38%,transparent 78%),linear-gradient(0deg,#141414,transparent 38%)}.hc{position:absolute;left:28px;bottom:38px;max-width:540px;z-index:1}.hc h2{font-size:30px;margin:0 0 6px}
.hc p{margin:5px 0 10px;font-size:14px;color:#ddd;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.hp{position:absolute;right:70px;top:20px;height:300px;border-radius:6px;box-shadow:0 6px 30px #000a}
.dots{position:absolute;right:16px;bottom:12px;z-index:2;display:flex;gap:7px}.dot{width:10px;height:10px;border-radius:50%;background:#fff5;cursor:pointer}.dot.on{background:#fff}.pers{text-align:center;padding:14px}.pr{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #2a2a2a}
@media(max-width:600px){.hero{height:250px}.hc h2{font-size:22px}.hc{left:16px;bottom:30px;max-width:92%}.hp{display:none}}
.np{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:10px;color:#777;font-size:13px}
#vacio{text-align:center;color:var(--mu);padding:60px;display:none}
</style></head><body>
<header><div class="top"><h1 id="logo" style="cursor:pointer" title="Ir al inicio (sin filtros)">MI CATÁLOGO</h1>
<input id="q" type="search" placeholder="Buscar título, actor, director, país, año, idioma…" autocomplete="off">
<select id="bm"><option value="_a">Buscar en: todo</option><option value="n">Solo título</option><option value="_z">Todo + sinopsis</option></select>
<div class="seg"><button data-k="todos" class="on">Todo</button><button data-k="pelicula">Películas</button><button data-k="serie">Series</button></div>
<details class="menu" id="menu"><summary>⚙ Herramientas</summary><div class="mp">
<b>Agregar</b><a href="/escanear">+ Agregar disco o título</a>
<b>Organizar</b><a href="/colecciones">Colecciones</a><a href="/vincular">Vincular con TMDB</a><a href="/calidad">Calidad de datos</a>
<b>Datos</b><a href="/respaldos">Respaldos (nube)</a><a href="/api/exportar.csv">Exportar a CSV</a><a href="/importar">Restaurar desde CSV</a><a href="/publicar">Publicar versión web</a>
<b>Ayuda</b><a href="/ayuda">Cómo se usa</a></div></details></div>
<div class="top" style="margin-top:8px">
<select id="fu"></select><select id="cosel"></select><select id="vfil"><option value="">Mi lista: todo</option><option value="pendiente">⏱ Por ver</option><option value="visto">✓ Vistos</option><option value="sinver">Sin ver</option><option value="fav">♥ Favoritos</option><option value="disp">💾 Disponibles ahora</option></select><select id="ord"><option value="az">Orden: A–Z</option><option value="rec">Recién agregadas</option><option value="val">Mejor valoradas</option><option value="mia">Mi puntuación</option><option value="ant">Año: nuevas primero</option><option value="vie">Año: antiguas primero</option></select><button id="rnd" class="tb">🎲 Sorpréndeme</button><button id="vt" class="tb">☰ Ver todo</button><button id="sl" class="tb" style="display:none">☑ Seleccionar</button><span id="n"></span></div>
<div class="chips" id="chips"></div><div class="abc" id="abc"></div></header>
<main id="m"></main><dialog id="dlg"></dialog><div id="bar" hidden></div><div id="vacio">No hay resultados con esos filtros.</div><p class="foot">Datos e imágenes: TMDB. Este producto usa la API de TMDB pero no está avalado ni certificado por TMDB.</p>
<script id="d" type="application/json">__DATOS__</script>
<script>
(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const D=JSON.parse(document.getElementById('d').textContent),TOP=__TOP__,ALL=__ALL__;
const norm=s=>(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
D.forEach((d,i)=>{d.i=i;d.l=(c=>/[a-z]/.test(c)?c.toUpperCase():'#')(norm(d.m).replace(/[^a-z0-9]/g,'')[0]||'#');d.n=norm(d.o+' '+d.t);d._a=norm([d.o,d.t,d.u,d.y,d.d,d.gu,d.a,d.pa,d.pr,d.mu,d.il,d.su,d.ce,d.g.join(' ')].join(' '));d._z=d._a+' '+norm(d.x)});
const PUBLICO=__PUBLICO__,WA=__WA__,FU=__FU__,CO=__CO__,dlg=document.getElementById('dlg');let tipo='todos',sel=new Set(),q='',fsel='',mod='_a',csel='',ini='',vf='',ord='az',lastF=[],EDIT=false,lim=120,lastSig='',vista='home',selm=false;const MARC=new Set();
function chips(){
  const vis=[...TOP,...[...sel].filter(g=>!TOP.includes(g))];
  const c=document.getElementById('chips');
  c.innerHTML=vis.map(g=>`<button data-g="${esc(g)}" class="${sel.has(g)?'on':''}">${esc(g)}</button>`).join('')+
   `<select id="mas"><option value="">Más géneros…</option>${ALL.filter(a=>!vis.includes(a[0])).map(a=>`<option value="${esc(a[0])}">${esc(a[0])} (${a[1]})</option>`).join('')}</select>`+
   (sel.size?'<button data-g="*">✕ Limpiar</button>':'');
}
const card=d=>`<article class="c${d.av===0?' off':''}${MARC.has(d.i)?' sel':''}"${d.av===0?' title="Disco no conectado"':''} data-i="${d.i}"><div class="im">
   ${d.p?'':`<div class="np">${esc(d.m)}</div>`}<img loading="lazy" src="${esc(d.p||'')}" alt="" onerror="this.style.visibility='hidden'">
   <div class="ov">${esc(d.x||'Sin sinopsis')}</div><span class="b ${d.k}">${d.k=='serie'?'Serie':'Película'}</span>${d.fv?'<span class="b" style="left:auto;right:7px;color:#ff5a7a">♥</span>':''}${d.es=='visto'?'<span class="b" style="top:auto;bottom:7px;background:#1a7f37">✓ Visto</span>':d.es=='pendiente'?'<span class="b" style="top:auto;bottom:7px;background:#8a6d00">Por ver</span>':''}</div>
   <h3>${esc(d.m)}</h3>${d.al?`<p>${esc(d.al)}</p>`:''}
   <p>${[d.y,d.r?'★ '+d.r.toFixed(1):''].filter(Boolean).join(' · ')}</p>
   ${d.g.length?`<p class="g">${esc(d.g.slice(0,3).join(' · '))}</p>`:''}
   ${d.s?`<p class="s">${esc(d.s)}</p>`:''}${d.u?`<p class="f">Disco ${esc(d.u)}</p>`:''}${d.ec?`<p class="st5">${'★'.repeat(d.ec)}${'☆'.repeat(5-d.ec)}</p>`:''}</article>`;
function render(f){const m=document.getElementById('m');clearInterval(heroIv);
 const sig=[tipo,q,fsel,csel,ini,vf,ord,mod,[...sel].join()].join('|');if(sig!=lastSig){lim=120;lastSig=sig}
 if(inicio()){m.className='home';m.innerHTML=hero()+cintas();iniciarHero();return}
 m.className='';m.innerHTML=f.slice(0,lim).map(card).join('')+(f.length>lim?`<div class="mas"><button class="tb" onclick="lim+=120;draw()">Mostrar más (${f.length-lim} restantes)</button></div>`:'')}
function inicio(){return vista=='home'&&tipo=='todos'&&!q&&!fsel&&!csel&&!ini&&!vf&&ord=='az'&&!sel.size}
const VER={pend:{vf:'pendiente'},recp:{tipo:'pelicula',ord:'rec'},recs:{tipo:'serie',ord:'rec'},anim:{g:['Animación'],ord:'rec'},val:{ord:'val'},mia:{ord:'mia'},fav:{vf:'fav'}};
function irA(o){tipo=o.tipo||'todos';sel=new Set(o.g||[]);vf=o.vf||'';ord=o.ord||'az';fsel='';csel='';ini='';q='';
 document.getElementById('q').value='';document.getElementById('vfil').value=vf;document.getElementById('ord').value=ord;
 ['fu','cosel'].forEach(id=>{const x=document.getElementById(id);if(x)x.value=''});
 document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.k==tipo));chips();letras();draw();window.scrollTo(0,0)}
let heroIv=0,heroI=0,heroL=null;
const FILAS={est:'🎃 Estacional (Halloween, Navidad, verano)',gusto:'✨ Porque te gusta…',recp:'🆕 Películas agregadas recientemente',recs:'🆕 Series agregadas recientemente',anim:'🎨 Animación reciente',pend:'⏱ Por ver',val:'⭐ Mejor valoradas',mia:'⭐ Tus mejor puntuadas',fav:'♥ Tus favoritos'};
const ORDEN0=['est','gusto','recp','recs','anim','pend','val','mia','fav'];
function cfgCintas(){try{return JSON.parse(localStorage.getItem('cat_cintas')||'{}')}catch(e){return{}}}
function guardarCfg(c){try{localStorage.setItem('cat_cintas',JSON.stringify(c))}catch(e){}}
function ordenCintas(){const o=(cfgCintas().orden||[]).filter(i=>FILAS[i]);ORDEN0.forEach(i=>{if(!o.includes(i))o.push(i)});return o}
function personalizar(){const oc=new Set(cfgCintas().ocultas||[]),o=ordenCintas();
 dlg.innerHTML=`<button class="x" onclick="dlg.close()">✕</button><h2>Personalizar inicio</h2><p style="color:var(--mu)">Cambia el orden de las filas o escóndelas. Se guarda en este navegador. Las filas sin títulos suficientes no aparecen.</p>`
  +o.map((id,i)=>`<div class="pr"><label><input type="checkbox" ${oc.has(id)?'':'checked'} onchange="ocultarCinta('${id}',this.checked)"> ${FILAS[id]}</label><span><button class="tb" ${i?'':'disabled'} onclick="moverCinta('${id}',-1)">▲</button> <button class="tb" ${i<o.length-1?'':'disabled'} onclick="moverCinta('${id}',1)">▼</button></span></div>`).join('')
  +`<div class="pr"><label><input type="checkbox" ${cfgCintas().sinhero?'':'checked'} onchange="toggleHero(this.checked)"> Mostrar el banner de destacados (solo aparece si hay imágenes grandes descargadas)</label></div><p><button class="tb" onclick="guardarCfg({});personalizar();draw()">Restablecer orden</button></p>`;if(!dlg.open)dlg.showModal()}
function toggleHero(v){const c=cfgCintas();c.sinhero=!v;guardarCfg(c);draw()}
function moverCinta(id,d){const o=ordenCintas(),i=o.indexOf(id),j=i+d;if(j<0||j>=o.length)return;[o[i],o[j]]=[o[j],o[i]];const c=cfgCintas();c.orden=o;guardarCfg(c);personalizar();draw()}
function ocultarCinta(id,mostrar){const c=cfgCintas(),oc=new Set(c.ocultas||[]);mostrar?oc.delete(id):oc.add(id);c.ocultas=[...oc];guardarCfg(c);draw()}
function cintas(){const mes=new Date().getMonth()+1,rnd=a=>[...a].sort(()=>Math.random()-.5),rec=(a,b)=>(b.fe||'').localeCompare(a.fe||'')||b.db-a.db,
 tiene=(d,gs)=>d.g.some(g=>gs.includes(g)),R={};
 const add=(id,t,l,ver,min=3)=>{if(l.length>=min)R[id]=`<section class="rowc"><h2>${t}${ver?` <a href="#" data-v="${ver}">Ver todo ›</a>`:''}</h2><div class="rail big">${l.slice(0,15).map(card).join('')}</div></section>`};
 add('pend','⏱ Por ver',D.filter(d=>d.es=='pendiente'),'pend',2);
 const est=mes==10?['🎃 Para Halloween',D.filter(d=>tiene(d,['Terror','Suspense','Thriller','Sobrenatural','Intriga'])&&d.es!='visto')]
  :mes==12?['🎄 Para Navidad',D.filter(d=>/navidad|christmas|santa claus|papá noel|noel/i.test([d.m,d.o,d.t,d.x].join(' ')))]
  :(mes==1||mes==2)?['☀️ Para el verano',D.filter(d=>tiene(d,['Aventuras','Comedia','Familia','Animación'])&&d.es!='visto')]:null;
 if(est)add('est',est[0],rnd(est[1]));
 const perfil={};D.forEach(d=>{const w=(d.fv?3:0)+(d.es=='visto'?1:0)+(d.es=='visto'&&(d.r||0)>=8?1:0)+(d.ec>=4?2:0);if(w)d.g.forEach(g=>perfil[g]=(perfil[g]||0)+w)});
 const top=Object.entries(perfil).sort((a,b)=>b[1]-a[1]).slice(0,2).map(e=>e[0]);
 if(top.length)add('gusto','✨ Porque te gusta: '+top.join(' y '),rnd(D.filter(d=>d.es!='visto'&&!d.fv).map(d=>({d,s:d.g.reduce((a,g)=>a+(perfil[g]||0),0)})).filter(o=>o.s>0).sort((a,b)=>b.s-a.s).slice(0,30).map(o=>o.d)));
 add('recp','🆕 Películas agregadas recientemente',D.filter(d=>d.k=='pelicula').sort(rec),'recp');
 add('recs','🆕 Series agregadas recientemente',D.filter(d=>d.k=='serie').sort(rec),'recs');
 add('anim','🎨 Animación reciente',D.filter(d=>d.g.includes('Animación')).sort(rec),'anim');
 add('val','⭐ Mejor valoradas',D.filter(d=>d.r).sort((x,y)=>y.r-x.r),'val');
 add('mia','⭐ Tus mejor puntuadas',D.filter(d=>d.ec>=4).sort((x,y)=>y.ec-x.ec),'mia',2);
 add('fav','♥ Tus favoritos',D.filter(d=>d.fv),'fav',2);
 const oc=new Set(cfgCintas().ocultas||[]),filas=ordenCintas().filter(id=>R[id]&&!oc.has(id)).map(id=>R[id]);
 return (filas.join('')||'<p style="color:var(--mu)">Aún no hay filas para mostrar.</p>')+'<p class="pers"><button class="tb" onclick="personalizar()">✎ Personalizar inicio</button></p>'}
function destacados(){const perfil={};D.forEach(d=>{const w=(d.fv?3:0)+(d.es=='visto'?1:0)+(d.ec>=4?2:0);if(w)d.g.forEach(g=>perfil[g]=(perfil[g]||0)+w)});
 const pool=D.filter(d=>d.x&&d.es!='visto'&&d.fo).map(d=>({d,s:d.g.reduce((a,g)=>a+(perfil[g]||0),0)+(d.fo?4:0)+(d.r||0)/2+Math.random()*3})).sort((a,b)=>b.s-a.s);
 const out=[],cols=new Set();for(const o of pool){if(o.d.co&&cols.has(o.d.co))continue;if(o.d.co)cols.add(o.d.co);out.push(o.d);if(out.length==4)break}return out}
function hero(){if(cfgCintas().sinhero)return '';heroL=heroL||destacados();if(!heroL.length)return '';
 return `<section class="hero" id="hero">${heroL.map((d,k)=>`<div class="hs${k?'':' on'}"${d.fo?` style="background-image:url('${esc(d.fo)}')"`:''}><div class="hc"><h2>${esc(d.m)}</h2>
  <p style="color:#bbb;font-size:13px">${[d.k=='serie'?'Serie':'Película',d.y,d.du?d.du+' min':'',d.r?'★ '+d.r.toFixed(1):'',d.g.slice(0,3).join(' · ')].filter(Boolean).map(esc).join(' · ')}</p><p>${esc(d.x)}</p>
  <button class="tb" style="background:#fff;color:#000" onclick="abrirFicha(${d.i})">ℹ Ver ficha</button>${EDIT&&d.ru&&d.av!==0?` <button class="tb" onclick="heroPlay(${d.i})">▶ Reproducir</button>`:''}</div></div>`).join('')}
  <div class="dots">${heroL.map((d,k)=>`<span class="dot${k?' ':' on'}" data-h="${k}"></span>`).join('')}</div></section>`}
function iniciarHero(){clearInterval(heroIv);heroI=0;const n=document.querySelectorAll('.hs').length;if(n>1)heroIv=setInterval(()=>mostrarSlide((heroI+1)%n),8000)}
function mostrarSlide(k){heroI=k;document.querySelectorAll('.hs').forEach((x,i)=>x.classList.toggle('on',i==k));document.querySelectorAll('.dot').forEach((x,i)=>x.classList.toggle('on',i==k))}
function abrirFicha(i){dlg.innerHTML=ficha(D[i]);dlg.showModal()}
function heroPlay(i){abrirFicha(i);abrir(i,'archivo')}
function playHtml(d){if(!EDIT||!d.ru)return '';
 const av=d.av===1?'<span style="color:#7fd48a">✓ Disponible</span>':d.av===0?`<span style="color:#f5c26b">⚠ Conecta el disco ${esc(d.u||'')}</span>`:d.av===2?'<span style="color:#ff8a8a">⚠ El archivo no está en su ruta</span>':'';
 return `<p><button class="tb" onclick="abrir(${d.i},'archivo')">▶ Reproducir</button> <button class="tb" onclick="abrir(${d.i},'carpeta')">📂 Abrir carpeta</button> <span id="av">${av}</span></p>`}
async function abrir(i,modo){const b=document.getElementById('av');
 try{const r=await(await fetch('/api/abrir',{method:'POST',body:JSON.stringify({id:D[i].db,modo})})).json();if(r.error)throw new Error(r.error);b.innerHTML='<span style="color:#7fd48a">Abriendo en tu reproductor…</span>'}
 catch(x){b.innerHTML=`<span style="color:#f5c26b">${esc(x.message.replace(/^\w+: /,''))}</span>`}}
function draw(){
  const W=q.split(/\s+/).filter(Boolean);const f=D.filter(d=>(tipo=='todos'||d.k==tipo)&&(!fsel||d.u==fsel)&&(!csel||d.co==csel)&&(!vf||(vf=='fav'?d.fv:vf=='sinver'?d.es!='visto':vf=='disp'?d.av===1:d.es==vf))&&(!ini||d.l==ini)&&W.every(w=>d[mod].includes(w))&&[...sel].every(g=>d.g.includes(g)));
  if(csel)f.sort((a,b)=>(a.y||9999)-(b.y||9999)||a.m.localeCompare(b.m));if(ord!='az')f.sort(({rec:(a,b)=>(b.fe||'').localeCompare(a.fe||'')||b.db-a.db,val:(a,b)=>(b.r||0)-(a.r||0),ant:(a,b)=>(b.y||0)-(a.y||0),vie:(a,b)=>(a.y||9999)-(b.y||9999),mia:(a,b)=>(b.ec||0)-(a.ec||0)})[ord]);lastF=f;document.getElementById('n').textContent=f.length+' títulos';
  document.getElementById('vacio').style.display=f.length?'none':'block';
  render(f);
}
document.getElementById('q').oninput=e=>{q=norm(e.target.value.trim());draw()};
document.querySelector('.seg').onclick=e=>{const k=e.target.dataset.k;if(!k)return;tipo=k;
  document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.k==k));draw()};
document.getElementById('chips').onclick=e=>{const g=e.target.dataset.g;if(!g)return;
  g=='*'?sel.clear():(sel.has(g)?sel.delete(g):sel.add(g));chips();draw()};
document.getElementById('chips').onchange=e=>{if(e.target.id=='mas'&&e.target.value){sel.add(e.target.value);chips();draw()}};
const fuEl=document.getElementById('fu');
fuEl.innerHTML='<option value="">Todos los discos</option>'+FU.map(a=>`<option value="${esc(a[0])}">${esc(a[0])} (${a[1]})</option>`).join('');
fuEl.onchange=e=>{fsel=e.target.value;draw()};
const fila=(k,v)=>v?`<p><b>${k}</b> ${esc(v)}</p>`:'';
function ficha(d){return `<button class="x" onclick="dlg.close()">✕</button>${EDIT?`<button class="ed" onclick="editar(${d.i})">✎ Editar</button>`:''}<div class="fi"><img src="${esc(d.p||'')}" alt=""><div>
 <h2>${esc(d.m)}</h2>${d.al?`<p>${esc(d.al)}</p>`:''}
 <p>${[d.k=='serie'?'Serie':'Película',d.y,d.du?d.du+' min':'',d.ce,d.pa,d.r?'★ '+d.r.toFixed(1):''].filter(Boolean).map(esc).join(' · ')}</p>
 <p class="g">${esc(d.g.join(' · '))}</p>${d.s?`<p class="s">${esc(d.s)}</p>`:''}
 ${marcasHtml(d)}<p class="sin">${esc(d.x||'Sin sinopsis')}</p>${d.no?`<p class="sin" style="color:#f5c26b">📝 ${esc(d.no)}</p>`:''}
 ${filaP('Director:',d.d)}${filaP('Guion:',d.gu)}${filaP('Reparto:',d.a)}${fila('Disco:',d.u)}${d.co?`<p><b>Colección:</b> <a href="#" data-c="${esc(d.co)}">${esc(d.co)}</a> <span style="color:var(--mu)">(${(CO.find(a=>a[0]==d.co)||[0,''])[1]} títulos)</span></p>`:''}${fila('Archivo:',d.ru)}${fila('Formato:',d.fm)}${fila('Idiomas:',d.il)}${fila('Subtítulos:',d.su)}
 ${playHtml(d)}${trailerHtml(d)}${d.ur?`<p><a href="${esc(d.ur)}" target="_blank" rel="noopener">Ver en línea</a></p>`:''}
 ${similaresHtml(d)}${d.c?`<details><summary>Comentarios</summary><p class="sin">${esc(d.c)}</p></details>`:''}</div></div>`}
document.getElementById('m').onclick=e=>{const h=e.target.dataset&&e.target.dataset.h;if(h!==undefined){mostrarSlide(+h);return}const v=e.target.dataset&&e.target.dataset.v;if(v){e.preventDefault();irA(VER[v]);return}const c=e.target.closest('.c');if(c&&selm){c.classList.toggle('sel');const i=+c.dataset.i;MARC.has(i)?MARC.delete(i):MARC.add(i);barra();return}if(c){dlg.innerHTML=ficha(D[c.dataset.i]);dlg.showModal()}};
dlg.onclick=e=>{if(e.target==dlg)dlg.close();const mi=e.target.closest&&e.target.closest('[data-o]');if(mi){dlg.innerHTML=ficha(D[+mi.dataset.o]);dlg.scrollTop=0;return}const cc=e.target.dataset&&e.target.dataset.c;if(cc){e.preventDefault();dlg.close();tipo='todos';fsel='';ini='';vf='';sel.clear();q='';document.getElementById('q').value='';csel=cc;['fu','vfil'].forEach(id=>{const x=document.getElementById(id);if(x)x.value=''});document.getElementById('cosel').value=cc;document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.k=='todos'));chips();letras();draw();window.scrollTo(0,0);return}const p=e.target.dataset&&e.target.dataset.p;if(p){e.preventDefault();dlg.close();tipo='todos';fsel='';csel='';ini='';vf='';sel.clear();['fu','cosel','vfil'].forEach(id=>{const x=document.getElementById(id);if(x)x.value=''});document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.k=='todos'));document.getElementById('q').value=p;q=norm(p);mod='_a';document.getElementById('bm').value='_a';chips();letras();draw()}};
function editar(i){const d=D[i];const c=(k,l,v,t='text')=>`<label class="ef">${l}<input type="${t}" name="${k}" value="${esc(v??'')}"></label>`;
 dlg.innerHTML=`<button class="x" onclick="dlg.close()">✕</button><h2>Editar: ${esc(d.m)}</h2><form id="ef">
 <label class="ef">Tipo<select name="tipo"><option value="pelicula" ${d.k=='pelicula'?'selected':''}>Película</option><option value="serie" ${d.k=='serie'?'selected':''}>Serie</option></select></label>${c('titulo_original','Título original',d.o)}${c('titulo_traducido','Título traducido',d.t)}${c('anio','Año',d.y,'number')}${c('fuente','Disco (fuente)',d.u)}
 ${d.k=='serie'?c('temporadas','Temporadas que tienes (ej: S01, S01-S03)',(d.st||'').replace(/\s*complet[ae]\b/ig,'').trim())+`<label class="ef"><input type="checkbox" name="completa" ${/complet[ae]/i.test(d.st||'')?'checked':''} style="display:inline;width:auto"> Serie completa (la serie terminó y tengo todas sus temporadas)</label>`:''}
 <label class="ef">Sinopsis<textarea name="sinopsis" rows="6">${esc(d.x||'')}</textarea></label>
 ${c('director','Director',d.d)}<label class="ef">Reparto (separado por comas)<textarea name="reparto" rows="3">${esc(d.a||'')}</textarea></label>${c('guion','Guion',d.gu)}${c('productora','Productora',d.pr)}${c('musica','Música',d.mu)}${c('pais','País',d.pa)}${c('certificacion','Clasificación',d.ce)}${c('duracion_min','Duración (min)',d.du,'number')}${c('calificacion','Valoración (0 a 10, vacío = sin valoración)',d.r,'number')}${c('generos','Géneros (separados por coma)',d.g.join(', '))}${c('coleccion','Colección (ej: Rambo)',d.co)}<datalist id="cl">${CO.map(a=>`<option value="${esc(a[0])}">`).join('')}</datalist>${c('trailer','Tráiler (URL de YouTube)',d.tr?'https://www.youtube.com/watch?v='+d.tr:'')}<label class="ef">Mi nota personal<textarea name="nota" rows="3">${esc(d.no||'')}</textarea></label><label class="ef">Cambiar carátula (archivo de imagen)<input type="file" name="pfile" accept="image/*"></label>
 ${c('purl','…o pega la URL de una imagen','')}
 <button>Guardar</button> <button type="button" class="tb" id="rfr">↻ Actualizar desde TMDB</button> <button type="button" id="del" style="background:#7a1010;margin-left:12px">Eliminar título</button> <span id="em"></span></form>`;
 document.querySelector('[name=coleccion]').setAttribute('list','cl');document.querySelector('[name=calificacion]').step='any';document.getElementById('rfr').onclick=async()=>{const em=document.getElementById('em');em.textContent='Consultando TMDB…';try{const r=await fetch('/api/refrescar',{method:'POST',body:JSON.stringify({id:d.db})});const j=await r.json();if(j.error)throw new Error(j.error);if(!j.corregidos){em.textContent='Nada que corregir: ya está en alfabeto latino.';return}recargar()}catch(x){em.textContent=x.message}};document.getElementById('del').onclick=async()=>{if(!confirm('¿Eliminar "'+d.m+'" del catálogo? No borra el archivo de tu disco.'))return;const r=await fetch('/api/borrar',{method:'POST',body:JSON.stringify({id:d.db})});const j=await r.json();if(j.error)alert(j.error);else recargar()};document.getElementById('ef').onsubmit=async e=>{e.preventDefault();const f=e.target.elements,campos=Object.fromEntries(new FormData(e.target)),arch=f.pfile.files[0],purl=(campos.purl||'').trim(),comp=!!campos.completa;
  delete campos.pfile;delete campos.purl;delete campos.completa;
  if(d.k=='serie')campos.temporadas=((campos.temporadas||'').replace(/\s*complet[ae]\b/ig,'').trim()+(comp?' Completa':'')).trim();
  const post=async(u,b)=>{const r=await fetch(u,{method:'POST',body:JSON.stringify(b)});const j=await r.json();if(j.error)throw new Error(j.error);return j};
  try{document.getElementById('em').textContent='Guardando…';await post('/api/editar',{id:d.db,campos});
   if(arch||purl){const imagen=arch?await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.readAsDataURL(arch)}):null;await post('/api/portada',{id:d.db,imagen,url:purl})}
   recargar()}catch(x){document.getElementById('em').textContent=x.message}}}
document.getElementById('bm').onchange=e=>{mod=e.target.value;draw()};
const coEl=document.getElementById('cosel');
if(CO.length){coEl.innerHTML='<option value="">Todas las colecciones</option>'+CO.map(a=>`<option value="${esc(a[0])}">${esc(a[0])} (${a[1]})</option>`).join('');coEl.onchange=e=>{csel=e.target.value;draw()}}else coEl.remove();
const abc=document.getElementById('abc');
function letras(){abc.innerHTML=['',...'ABCDEFGHIJKLMNOPQRSTUVWXYZ#'].map(l=>`<button data-l="${l}" class="${l==ini?'on':''}">${l||'Todas'}</button>`).join('')}
abc.onclick=e=>{if(e.target.dataset.l===undefined)return;ini=e.target.dataset.l;letras();draw()};letras();
document.getElementById('vfil').onchange=e=>{vf=e.target.value;draw()};
document.getElementById('ord').onchange=e=>{ord=e.target.value;draw()};
document.getElementById('rnd').onclick=()=>{if(!lastF.length)return;dlg.innerHTML=ficha(lastF[Math.floor(Math.random()*lastF.length)]);dlg.showModal()};
function personas(t){return String(t).split(/\s*,\s*|\s*\n\s*/).filter(Boolean).map(n=>`<a href="#" data-p="${esc(n)}">${esc(n)}</a>`).join(', ')}
function filaP(k,v){return v?`<p><b>${k}</b> ${personas(v)}</p>`:''}
function marcasHtml(d){return EDIT?`<p><button class="tb ${d.es=='visto'?'on':''}" onclick="marcar(${d.i},'es','visto')">✓ Visto</button> <button class="tb ${d.es=='pendiente'?'on':''}" onclick="marcar(${d.i},'es','pendiente')">⏱ Por ver</button> <button class="tb ${d.fv?'on':''}" onclick="marcar(${d.i},'fv')">♥ Favorito</button> <span class="stars">${[1,2,3,4,5].map(n=>`<span class="star${(d.ec||0)>=n?' on':''}" onclick="puntuar(${d.i},${n})">★</span>`).join('')}</span></p>`:''}
async function marcar(i,k,v){const d=D[i],b={id:d.db},antes={es:d.es,fv:d.fv};
 if(k=='es'){d.es=d.es==v?null:v;b.estado=d.es||''}else{d.fv=d.fv?0:1;b.favorito=!!d.fv}
 dlg.innerHTML=ficha(d);draw();guardarMarca(b,()=>{d.es=antes.es;d.fv=antes.fv})}
async function guardarMarca(b,deshacer){try{const r=await fetch('/api/marcar',{method:'POST',body:JSON.stringify(b)});const j=await r.json();if(j.error)throw new Error(j.error)}
 catch(x){deshacer();const d=D.find(y=>y.db==b.id);if(dlg.open&&d)dlg.innerHTML=ficha(d);draw();alert('No se pudo guardar el cambio: '+x.message)}}
if(!PUBLICO&&location.protocol.startsWith('http'))fetch('/api/estado').then(r=>r.json()).then(j=>{EDIT=j.local;D.forEach(d=>{const e=j.e[d.db];d.es=e?e[0]:null;d.fv=e?e[1]:0;d.no=e?e[2]:null;d.ec=e?(e[3]||0):0});document.getElementById('menu').style.display=EDIT?'':'none';document.getElementById('sl').style.display=EDIT?'':'none';draw();if(EDIT)fetch('/api/disponibilidad').then(r=>r.json()).then(av=>{D.forEach(d=>{d.av=av[d.db]});draw()}).catch(()=>{});const h=location.hash.match(/^#t(\d+)$/);if(h){const d=D.find(x=>x.db==+h[1]);if(d){dlg.innerHTML=ficha(d);dlg.showModal()}}}).catch(()=>{});
function parecidos(d){const dirs=(d.d||'').split(/\s*,\s*/).filter(Boolean),ac=new Set((d.a||'').split(/\s*,\s*|\n/).map(t=>t.trim()).filter(Boolean));
 return D.filter(x=>x!==d).map(x=>{let s=0;x.g.forEach(g=>{if(d.g.includes(g))s+=2});if(d.co&&x.co==d.co)s+=6;if(x.d&&dirs.some(n=>x.d.includes(n)))s+=4;
  let c=0;(x.a||'').split(/\s*,\s*|\n/).forEach(n=>{if(ac.has(n.trim()))c++});s+=Math.min(c,3)*2;if(x.k==d.k)s+=1;if(d.y&&x.y&&Math.abs(d.y-x.y)<=5)s+=1;if(d.pa&&x.pa&&x.pa==d.pa)s+=1;return{x,s}})
  .filter(o=>o.s>=5).sort((a,b)=>b.s-a.s).slice(0,10).map(o=>o.x)}
function similaresHtml(d){const l=parecidos(d);
 return (l.length?`<h4>Parecidos en tu catálogo</h4><div class="rail">${l.map(x=>`<div class="mini" data-o="${x.i}"><img src="${esc(x.p||'')}" alt=""><span>${esc(x.m)}</span><small>${x.y||''}</small></div>`).join('')}</div>`:'')
  +(EDIT?`<p><button class="tb" onclick="verSimilares(${d.i})">✨ Recomendados por TMDB</button></p><div id="sim"></div>`:'')}
async function verSimilares(i){const d=D[i],b=document.getElementById('sim');b.innerHTML='<span style="color:var(--mu)">Consultando TMDB…</span>';
 try{const r=await(await fetch('/api/similares?id='+d.db)).json();if(r.error)throw new Error(r.error);
  b.innerHTML='<h4>Recomendados por TMDB</h4><div class="rail">'+r.map(x=>{const loc=x.en_catalogo?D.find(y=>y.db==x.en_catalogo):null,img=x.poster?`<img src="https://image.tmdb.org/t/p/w185${x.poster}" alt="">`:'<div class="ph"></div>';
   return loc?`<div class="mini" data-o="${loc.i}">${img}<span>${esc(x.titulo)}</span><small class="si">✓ lo tienes</small></div>`:`<a class="mini" href="https://www.themoviedb.org/${x.media}/${x.id}" target="_blank" rel="noopener">${img}<span>${esc(x.titulo)}</span><small>no lo tienes</small></a>`}).join('')+'</div>'}
 catch(x){b.innerHTML=`<span style="color:var(--mu)">${esc(x.message)}</span>`}}
function barra(){const b=document.getElementById('bar');b.hidden=!selm;document.body.classList.toggle('selm',selm);if(!selm)return;if(PUBLICO){b.innerHTML=`<b>${MARC.size} elegidos</b> <button class="tb" style="background:#1a7f37" onclick="pedir()">📲 Enviar pedido por WhatsApp</button> <button class="tb" onclick="copiarPedido()">📋 Copiar lista</button> <button class="tb" onclick="MARC.clear();draw();barra()">Ninguno</button>`;return}
 b.innerHTML=`<b>${MARC.size} seleccionados</b> <button class="tb" onclick="selTodos()">Todos los visibles (${lastF.length})</button> <button class="tb" onclick="MARC.clear();draw();barra()">Ninguno</button>
 <button class="tb" onclick="lotePedir('coleccion','Nombre de la colección (déjalo vacío para quitarla):')">Colección…</button>
 <button class="tb" onclick="lote('estado','visto')">✓ Visto</button><button class="tb" onclick="lote('estado','pendiente')">⏱ Por ver</button><button class="tb" onclick="lote('favorito',1)">♥ Favorito</button>
 <select onchange="masAcc(this)"><option value="">Más acciones…</option><option value="tipo:pelicula">Cambiar a película</option><option value="tipo:serie">Cambiar a serie</option><option value="estado:">Quitar visto / por ver</option><option value="genero">Agregar género…</option></select>
 <button class="tb" style="background:#7a1010" onclick="lote('eliminar')">🗑 Eliminar</button>`}
function selTodos(){lastF.forEach(d=>MARC.add(d.i));draw();barra()}
function masAcc(s){const v=s.value;s.value='';if(!v)return;if(v=='genero')return lotePedir('agregar_genero','Género a agregar (ej: Terror):');const p=v.split(':');lote(p[0],p[1])}
function lotePedir(a,t){const v=prompt(t);if(v===null)return;lote(a,v)}
async function lote(accion,valor){if(!MARC.size){alert('No hay títulos seleccionados.');return}
 if(accion=='eliminar'&&!confirm(`¿Eliminar ${MARC.size} títulos del catálogo? No borra los archivos de tus discos.`))return;
 try{const r=await(await fetch('/api/lote',{method:'POST',body:JSON.stringify({ids:[...MARC].map(i=>D[i].db),accion,valor})})).json();if(r.error)throw new Error(r.error);recargar()}catch(x){alert(x.message)}}
document.getElementById('sl').onclick=()=>{selm=!selm;MARC.clear();if(selm&&inicio()){vista='todo';document.getElementById('vt').textContent='🏠 Inicio'}document.getElementById('sl').textContent=selm?(PUBLICO?'✕ Cancelar pedido':'✕ Salir de selección'):(PUBLICO?'🛒 Hacer un pedido':'☑ Seleccionar');draw();barra()};
document.getElementById('vt').onclick=()=>{vista=vista=='home'?'todo':'home';document.getElementById('vt').textContent=vista=='home'?'☰ Ver todo':'🏠 Inicio';draw()};
async function puntuar(i,n){const d=D[i],antes=d.ec||0;d.ec=antes==n?0:n;dlg.innerHTML=ficha(d);draw();guardarMarca({id:d.db,estrellas:d.ec},()=>{d.ec=antes})}
function recargar(){try{sessionStorage.setItem('cat_estado',JSON.stringify({tipo,qv:document.getElementById('q').value,fsel,csel,ini,vf,ord,mod,sel:[...sel],vista,y:window.scrollY}))}catch(e){}location['reload']()}
function restaurar(){try{const s=JSON.parse(sessionStorage.getItem('cat_estado')||'null');if(!s)return;sessionStorage.removeItem('cat_estado');
 tipo=s.tipo;fsel=s.fsel;csel=s.csel;ini=s.ini;vf=s.vf;ord=s.ord;mod=s.mod;sel=new Set(s.sel);vista=s.vista;document.getElementById('q').value=s.qv;q=norm(s.qv);
 const set=(id,v)=>{const x=document.getElementById(id);if(x)x.value=v};set('fu',fsel);set('cosel',csel);set('vfil',vf);set('ord',ord);set('bm',mod);
 document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.k==tipo));document.getElementById('vt').textContent=vista=='home'?'☰ Ver todo':'🏠 Inicio';
 chips();letras();draw();setTimeout(()=>window.scrollTo(0,s.y),60)}catch(e){}}
document.getElementById('logo').onclick=()=>{vista='home';selm=false;MARC.clear();document.getElementById('sl').textContent=PUBLICO?'🛒 Hacer un pedido':'☑ Seleccionar';document.getElementById('vt').textContent='☰ Ver todo';irA({});barra()};
function textoPedido(){return 'Hola! Me gustaría pedirte estos títulos:\n'+[...MARC].map(i=>'• '+D[i].m+(D[i].y?' ('+D[i].y+')':'')+(D[i].k=='serie'?' [serie]':'')).join('\n')}
function pedir(){if(!MARC.size){alert('Elige al menos un título.');return}window.open('https://wa.me/'+WA+'?text='+encodeURIComponent(textoPedido()),'_blank')}
async function copiarPedido(){if(!MARC.size)return;try{await navigator.clipboard.writeText(textoPedido());alert('Lista copiada. Pégala en el mensaje que quieras.')}catch(e){prompt('Copia esta lista:',textoPedido())}}
dlg.onclose=()=>{dlg.innerHTML=''};
function trailerHtml(d){const web=location.protocol.startsWith('http');
 if(d.tr)return `<p>${web?`<button class="tb" onclick="verTrailer(${d.i})">▶ Ver tráiler</button> `:''}<a href="https://www.youtube.com/watch?v=${d.tr}" target="_blank" rel="noopener">Abrir en YouTube</a></p><div id="tv"></div>`;
 return EDIT?`<p><button class="tb" onclick="buscarTrailer(${d.i})">🔍 Buscar tráiler</button> <span id="tm"></span></p>`:''}
function verTrailer(i){document.getElementById('tv').innerHTML=`<iframe src="https://www.youtube-nocookie.com/embed/${D[i].tr}?autoplay=1" allow="autoplay; encrypted-media; fullscreen" allowfullscreen style="width:100%;aspect-ratio:16/9;border:0;border-radius:8px;margin-top:8px"></iframe>`}
async function buscarTrailer(i){const m=document.getElementById('tm');m.textContent='Buscando…';try{const r=await fetch('/api/trailer',{method:'POST',body:JSON.stringify({id:D[i].db})});const j=await r.json();if(j.error)throw new Error(j.error);D[i].tr=j.trailer;dlg.innerHTML=ficha(D[i])}catch(x){m.textContent=x.message}}
if(PUBLICO){document.getElementById('menu').style.display='none';document.getElementById('vfil').style.display='none';const sl=document.getElementById('sl');sl.style.display='';sl.textContent='🛒 Hacer un pedido'}
if(!FU.length)document.getElementById('fu').style.display='none';
document.addEventListener('click',e=>{const m=document.getElementById('menu');if(m&&m.open&&!m.contains(e.target))m.open=false});
document.addEventListener('keydown',e=>{if(e.key=='Escape'){const m=document.getElementById('menu');if(m)m.open=false}});
chips();draw();restaurar();
</script></body></html>"""


def generar(carpeta, publico=False, whatsapp="", salida=None):
    """Genera index.html en `salida` (por defecto, la misma carpeta). publico=True: versión de solo lectura para compartir."""
    carpeta = Path(carpeta)
    salida = Path(salida) if salida else carpeta
    db = sqlite3.connect(carpeta / "catalogo.db")
    _cols = {r[1] for r in db.execute("PRAGMA table_info(titulos)")}
    for _c, _t in (("trailer", "TEXT"), ("coleccion", "TEXT"), ("estado", "TEXT"), ("favorito", "INTEGER"), ("nota", "TEXT"), ("estrellas", "INTEGER"), ("fondo", "TEXT")):      # migración: catálogos creados antes de estas funciones
        if _c not in _cols:
            db.execute(f"ALTER TABLE titulos ADD COLUMN {_c} {_t}")
    db.commit()
    datos = []
    for lid, tipo, orig, trad, anio, cal, gen, temp, port, fu, sin, com, dire, rep, pais, cert, dur, ruta, fv, fa, res, idi, sub, url, gui, prod, mus, tra, col, est, fav, nt, fec, est5, fon in db.execute(
            "SELECT id,tipo,titulo_original,titulo_traducido,anio,calificacion,generos,temporadas,portada,fuente,sinopsis,comentarios,director,reparto,pais,certificacion,duracion_min,ruta_archivo,formato_video,formato_audio,resolucion,idiomas,subtitulos,url,guion,productora,musica,trailer,coleccion,estado,favorito,nota,fecha_agregado,estrellas,fondo FROM titulos"):
        datos.append({"db": lid, "st": temp, "m": mostrar(orig, trad), "al": alternativo(orig, trad), "o": (orig or "").strip(), "t": (trad or "").strip(), "k": tipo, "y": anio, "r": cal,
                      "g": generos(gen), "s": temporadas(temp) if tipo == "serie" else None, "p": port, "u": fu, "x": sin, "c": com,
                      "d": dire, "a": rep, "pa": pais, "ce": cert, "du": dur, "ru": ruta,
                      "fm": " · ".join(filter(None, [fv, fa, res])), "il": idi, "su": sub, "ur": url, "gu": gui, "pr": prod, "mu": mus, "tr": tra, "co": col, "es": est, "fv": fav or 0, "no": nt, "fe": fec, "ec": est5 or 0, "fo": fon if fon and fon.startswith("fondos/") else None})
    datos.sort(key=lambda x: clave_orden(x["m"]))
    if publico:      # la versión pública no lleva rutas, notas, marcas personales ni datos técnicos
        for d in datos:
            for k in ("ru", "no", "es", "fv", "ec", "u", "fm", "il", "su", "av"):
                d.pop(k, None)
        con_fondo = sorted([d for d in datos if d.get("fo")], key=lambda d: -(d.get("r") or 0))[:40]      # la web liviana lleva pocas imágenes anchas
        mantener = {d["db"] for d in con_fondo}
        for d in datos:
            if d["db"] not in mantener:
                d["fo"] = None
    cuenta = Counter(g for d in datos for g in d["g"])
    html = PLANTILLA
    todos = cuenta.most_common()
    html = (html.replace("__DATOS__", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__TOP__", json.dumps([g for g, _ in todos[:18]], ensure_ascii=False))
                .replace("__ALL__", json.dumps(todos, ensure_ascii=False))
                .replace("__FU__", json.dumps(sorted(Counter(d.get("u") for d in datos if d.get("u")).items()), ensure_ascii=False))
                .replace("__CO__", json.dumps(sorted(Counter(d["co"] for d in datos if d["co"]).items()), ensure_ascii=False))
                .replace("__PUBLICO__", "true" if publico else "false")
                .replace("__WA__", json.dumps(re.sub(r"\D", "", whatsapp or ""))))
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "index.html").write_text(html, encoding="utf-8")
    print(f"index.html listo: {len(datos)} títulos, {len(todos)} géneros")


if __name__ == "__main__":
    generar(sys.argv[1] if len(sys.argv) > 1 else "catalogo")
