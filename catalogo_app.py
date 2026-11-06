#!/usr/bin/env python3
"""Aplicación local: escanea un disco y agrega películas/series al catálogo con datos de TMDB.
Uso:  python catalogo_app.py        (abre http://localhost:8765)
Necesita config.json con {"tmdb_api_key": "..."}  y:  pip install pillow"""
import base64, csv, datetime, io, json, ntpath, os, shutil, time, unicodedata, re, sqlite3, subprocess, sys, threading, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from importar_amc import guardar_portada

AQUI = Path(__file__).resolve().parent
CARPETA = AQUI / "catalogo"
CLAVE = ""
MIN_BYTES = 20_000_000          # ignora archivos menores (samples, trailers)
VIDEO = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".mpg", ".mpeg", ".ts", ".divx", ".flv", ".m2ts", ".mts", ".vob", ".iso", ".rmvb", ".webm", ".asf", ".ogm", ".3gp"}
_GUARDAR = threading.Lock()
RUIDO = re.compile(r"\b(1080p|720p|480p|2160p|4k|hdrip|bdrip|brrip|dvdrip|bluray|blu-ray|web-?dl|webrip|hdtv|"
                   r"x264|x265|h264|h265|hevc|xvid|divx|ac3|aac|dts|latino|castellano|spanish|multi|extended|"
                   r"remastered|proper|repack)\b.*", re.I)
GEN = {"Aventura": "Aventuras", "Fantasía": "Fantástico", "Misterio": "Intriga", "Bélica": "Bélico",
       "Historia": "Histórico", "Suspense": "Thriller", "Película de TV": None, "Action & Adventure": "Acción, Aventuras",
       "Sci-Fi & Fantasy": "Ciencia ficción, Fantástico", "War & Politics": "Bélico, Política", "Kids": "Infantil"}


def es_latino(s):
    return bool(s) and all(unicodedata.name(c, "").startswith("LATIN") for c in s if c.isalpha())


def conectar():
    return sqlite3.connect(CARPETA / "catalogo.db", timeout=30)      # espera hasta 30 s si otra tarea está escribiendo


# ---------- escaneo de carpetas ----------
def limpiar(nombre):
    """'Rambo.First.Blood.1982.1080p.x264' -> ('Rambo First Blood', 1982)"""
    n = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", nombre)
    n = re.sub(r"[._]+", " ", n)
    anio = None
    m = re.search(r"[\s(]((?:19\d\d|20[0-2]\d))(?!\d)", n)
    if m:
        anio, n = int(m[1]), n[:m.start()]
    n = RUIDO.sub("", n)
    n = re.sub(r"\(.*?\)", " ", n)
    return re.sub(r"\s+", " ", n).strip(" -"), anio


def temporadas_en(carpeta):
    s = set()
    for root, _, files in os.walk(carpeta):
        for t in [os.path.basename(root)] + files:
            for m in re.finditer(r"(?<![A-Za-z])S(\d{1,2})[ ._-]?E\d{1,3}|(?:season|temporada|temp)[ ._-]*(\d{1,2})|^S(\d{1,2})$", t, re.I):
                s.add(int(next(g for g in m.groups() if g)))
    return sorted(s)


def primer_video(carpeta):
    for root, _, files in os.walk(carpeta):
        for f in sorted(files):
            if Path(f).suffix.lower() in VIDEO:
                return str(Path(root) / f)


def escanear(ruta, tipo, fuente=None):
    """Lista lo nuevo de una carpeta (o un archivo suelto). Ya existente = misma ruta Y mismo disco."""
    ruta = Path(ruta.strip().strip('"'))
    if not ruta.exists():
        raise ValueError(f"No encuentro la ruta: {ruta}")
    fuente = (fuente or "").strip().upper()
    ya = [r[0].lower() for r in conectar().execute(
        "SELECT ruta_archivo FROM titulos WHERE ruta_archivo IS NOT NULL AND COALESCE(fuente,'')=?", (fuente,))]
    items = []
    if tipo == "pelicula":
        archivos = [ruta] if ruta.is_file() else [Path(r) / f for r, _, fs in os.walk(ruta) for f in sorted(fs)]
        for p in archivos:
            if p.suffix.lower() in VIDEO and (ruta.is_file() or os.path.getsize(p) >= MIN_BYTES) and str(p).lower() not in ya:
                t, a = limpiar(p.stem)
                items.append({"nombre": p.name, "ruta": str(p), "titulo": t, "anio": a})
    else:
        if ruta.is_file():
            raise ValueError("Para series indica la carpeta de la serie, o la carpeta que contiene varias series.")
        for d in sorted(x for x in ruta.iterdir() if x.is_dir()):
            if any(e.startswith(str(d).lower() + os.sep) for e in ya):
                continue
            v = primer_video(d)
            if v:
                t, a = limpiar(d.name)
                items.append({"nombre": d.name, "ruta": v, "titulo": t, "anio": a, "temporadas": temporadas_en(d)})
    return items


# ---------- TMDB ----------
def tmdb(ruta, **q):
    q["api_key"] = CLAVE
    q.setdefault("language", "es-MX")
    with urllib.request.urlopen(f"https://api.themoviedb.org/3/{ruta}?{urllib.parse.urlencode(q)}", timeout=20) as r:
        return json.load(r)


def descargar(url, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(1)


def item(media, x):
    return {"media": media, "id": x["id"], "titulo": x.get("title") or x.get("name"),
            "original": x.get("original_title") or x.get("original_name"),
            "anio": (x.get("release_date") or x.get("first_air_date") or "")[:4],
            "poster": x.get("poster_path"), "resumen": (x.get("overview") or "")[:150]}


def resolver(q, tipo, anio=None):
    """q puede ser un título, una URL de TMDB / IMDb / FilmAffinity, o un id tt1234567."""
    q, media = q.strip(), ("movie" if tipo == "pelicula" else "tv")
    m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", q)
    if m:
        return [item(m[1], tmdb(f"{m[1]}/{m[2]}"))]
    m = re.search(r"imdb\.com/title/(tt\d+)", q) or re.fullmatch(r"(tt\d+)", q)
    if m:
        r = tmdb(f"find/{m[1]}", external_source="imdb_id")
        return [item("movie", x) for x in r["movie_results"]] + [item("tv", x) for x in r["tv_results"]]
    if "filmaffinity.com" in q:
        raise ValueError("FilmAffinity no permite que un programa lea sus páginas. Busca el título en TMDB o pega la URL de TMDB o de IMDb.")
    p = {"query": q}
    clave_anio = "year" if media == "movie" else "first_air_date_year"
    if anio:
        p[clave_anio] = anio
    res = tmdb(f"search/{media}", **p)["results"]
    if not res and anio:
        p.pop(clave_anio)
        res = tmdb(f"search/{media}", **p)["results"]
    return [item(media, x) for x in res[:5]]


def normalizar_fuentes():
    """Unifica discos escritos distinto (e01 -> E01). Devuelve cuántas filas cambió."""
    db = conectar()
    n = db.execute("UPDATE titulos SET fuente=UPPER(TRIM(fuente)), tipo_medio=UPPER(TRIM(tipo_medio)) "
                   "WHERE fuente IS NOT NULL AND fuente != UPPER(TRIM(fuente))").rowcount
    db.commit(); db.close()
    return n


def editar(id_, campos):
    """Edita campos de una ficha (lista blanca) y regenera la página."""
    permitidos = {"titulo_original", "titulo_traducido", "anio", "fuente", "temporadas", "sinopsis", "tipo", "coleccion", "trailer", "generos", "director", "reparto", "guion", "productora", "musica", "pais", "certificacion", "duracion_min", "nota", "calificacion"}
    vals = {}
    for k, v in campos.items():
        if k not in permitidos:
            continue
        v = (v or "").strip() or None
        if k == "calificacion" and v:
            v = round(float(v.replace(",", ".")), 1)
            if not 0 <= v <= 10:
                raise ValueError("La valoración debe estar entre 0 y 10")
        if k in ("anio", "duracion_min"):
            v = int(v) if v else None
        if k == "fuente" and v:
            v = v.upper()
        if k in ("director", "reparto", "guion", "productora", "musica", "pais") and v:
            v = re.sub(r"\s*\n\s*", " ", v)
        if k == "generos" and v:
            v = ", ".join(x.strip() for x in v.split(",") if x.strip()) or None
        if k == "tipo" and v not in ("pelicula", "serie"):
            continue
        if k == "trailer" and v:
            v = clave_youtube(v)
            if not v:
                raise ValueError("No reconozco esa URL de YouTube")
        vals[k] = v
    if "fuente" in vals:
        vals["tipo_medio"] = vals["fuente"]
    if not vals:
        return
    db = conectar()
    db.execute(f"UPDATE titulos SET {','.join(k + '=?' for k in vals)} WHERE id=?", [*vals.values(), int(id_)])
    db.execute("INSERT INTO titulos_fts(titulos_fts) VALUES('rebuild')")
    db.commit(); db.close()
    regenerar()


def cambiar_portada(id_, imagen=None, url=None):
    """Reemplaza la carátula con un archivo (data URL en base64) o con la imagen de una URL."""
    if imagen:
        datos = base64.b64decode(imagen.split(",", 1)[-1])
    elif url:
        datos = descargar(url)
    else:
        raise ValueError("Indica una imagen o una URL")
    rel = f"portadas/{int(id_)}_{int(time.time())}.jpg"      # nombre nuevo: evita la caché del navegador
    try:
        guardar_portada(datos, CARPETA / rel)
    except Exception:
        raise ValueError("Eso no parece una imagen válida (usa JPG, PNG o WEBP)")
    db = conectar()
    viejo = db.execute("SELECT portada FROM titulos WHERE id=?", (int(id_),)).fetchone()
    db.execute("UPDATE titulos SET portada=? WHERE id=?", (rel, int(id_)))
    db.commit(); db.close()
    if viejo and viejo[0] and viejo[0] != rel:
        (CARPETA / viejo[0]).unlink(missing_ok=True)
    regenerar()


def clave_youtube(txt):
    txt = (txt or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})", txt) or re.fullmatch(r"([\w-]{11})", txt)
    return m[1] if m else None


def elegir_trailer(videos):
    ys = [v for v in videos if v.get("site") == "YouTube"]
    for kind in ("Trailer", "Teaser"):
        c = sorted((v for v in ys if v.get("type") == kind), key=lambda v: (v.get("iso_639_1") != "es", not v.get("official")))
        if c:
            return c[0]["key"]


def limpiar_coleccion(n):
    if not n:
        return None
    n = re.sub(r"^(colecci[oó]n|saga)\s+", "", n.strip(), flags=re.I)
    return re.sub(r"\s*[-–:]?\s*(colecci[oó]n|collection|saga)$", "", n, flags=re.I).strip() or None


def buscar_trailer(id_):
    """Busca el tráiler en TMDB (por la URL de TMDB de la ficha, o por título y año) y lo guarda."""
    db = conectar()
    tipo, orig, trad, anio, url = db.execute("SELECT tipo,titulo_original,titulo_traducido,anio,url FROM titulos WHERE id=?", (int(id_),)).fetchone()
    m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
    if m:
        media, tid, nom = m[1], m[2], orig
    else:
        res = []
        for q in dict.fromkeys(x for x in (orig, trad) if x):
            res = resolver(q, tipo, anio)
            if res:
                break
        if not res:
            raise ValueError("No encontré este título en TMDB (revisa que el tipo, película o serie, sea correcto)")
        media, tid, nom = res[0]["media"], res[0]["id"], f'{res[0]["titulo"]} ({res[0]["anio"]})'
    k = elegir_trailer(tmdb(f"{media}/{tid}/videos", include_video_language="es,en")["results"])
    if not k:
        raise ValueError(f"TMDB no tiene tráiler para {nom}")
    db.execute("UPDATE titulos SET trailer=? WHERE id=?", (k, int(id_))); db.commit(); db.close()
    regenerar()
    return {"trailer": k, "encontrado": nom}


ARTICULOS = {"the", "a", "an", "el", "la", "los", "las", "un", "una", "le", "les", "der", "die", "das"}


def sugerir_colecciones():
    """Agrupa títulos sin colección que empiezan con la misma palabra (películas y series juntas)."""
    db = conectar(); grupos = {}
    for id_, tipo, o, t, a in db.execute("SELECT id,tipo,titulo_original,titulo_traducido,anio FROM titulos WHERE coleccion IS NULL"):
        txt = o or t or ""
        pal = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFD", txt.lower()).encode("ascii", "ignore").decode())
        while pal and pal[0] in ARTICULOS:
            pal = pal[1:]
        if pal and len(pal[0]) >= 4 and not pal[0].isdigit():
            grupos.setdefault(pal[0], []).append({"id": id_, "tipo": tipo, "titulo": txt, "anio": a})
    out = [{"nombre": k.capitalize(), "items": sorted(v, key=lambda x: x["anio"] or 9999)} for k, v in grupos.items() if 2 <= len(v) <= 15]
    return sorted(out, key=lambda g: -len(g["items"]))


RESPALDO_EXTRA = None     # carpeta adicional (p. ej. una carpeta sincronizada con OneDrive, Google Drive o Dropbox)


def respaldar(maximo=10, prefijo="catalogo"):
    """Copia consistente de la base en catalogo/respaldos/ (y en la carpeta adicional, si la hay)."""
    carpeta = CARPETA / "respaldos"; carpeta.mkdir(exist_ok=True)
    nombre = f"{prefijo}_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.db"
    origen, copia = conectar(), sqlite3.connect(carpeta / nombre)
    origen.backup(copia); copia.close(); origen.close()
    destinos = [carpeta]
    if RESPALDO_EXTRA:
        try:
            extra = Path(RESPALDO_EXTRA); extra.mkdir(parents=True, exist_ok=True)
            shutil.copy2(carpeta / nombre, extra / nombre); destinos.append(extra)
        except Exception as e:
            print("No pude copiar el respaldo a la carpeta adicional:", e)
    for d in destinos:
        for viejo in sorted(d.glob(f"{prefijo}_*.db"))[:-maximo]:
            viejo.unlink()
    return nombre


def exportar_csv():
    cur = conectar().execute("SELECT * FROM titulos ORDER BY id")
    sal = io.StringIO(); w = csv.writer(sal)
    w.writerow([c[0] for c in cur.description]); w.writerows(cur)
    return sal.getvalue()


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode()).strip()


def revisar_calidad():
    filas = conectar().execute("SELECT id,tipo,titulo_original,titulo_traducido,anio,fuente,sinopsis,portada,ruta_archivo FROM titulos").fetchall()
    ficha = lambda f: {"id": f[0], "tipo": f[1], "titulo": f[2] or f[3], "anio": f[4], "fuente": f[5]}
    res = {"sin_portada": [], "sin_anio": [], "sin_sinopsis": [], "sin_archivo": []}
    grupos, desconectados = {}, 0
    for f in filas:
        k = _norm(f[2] or f[3])
        if k:
            grupos.setdefault(k, []).append(f)
        if not f[7] or not (CARPETA / f[7]).exists():
            res["sin_portada"].append(ficha(f))
        if not f[4]:
            res["sin_anio"].append(ficha(f))
        if not f[6]:
            res["sin_sinopsis"].append(ficha(f))
        if f[8] and not os.path.exists(f[8]):
            unidad = ntpath.splitdrive(f[8])[0]
            if unidad and not os.path.exists(unidad + "\\"):
                desconectados += 1                      # el disco no está conectado: no se puede revisar
            else:
                res["sin_archivo"].append(ficha(f))
    res["duplicados"] = [[ficha(f) for f in g] for g in grupos.values()
                         if len(g) > 1 and (None in [x[4] for x in g] or len({x[4] for x in g}) < len(g))]
    cuenta = {}
    for f in filas:
        if f[8]:
            cuenta[(f[8].lower(), f[5] or "")] = cuenta.get((f[8].lower(), f[5] or ""), 0) + 1
    res["exactos"] = sum(c - 1 for c in cuenta.values() if c > 1)
    res["desconectados"] = desconectados
    return res


CODEC_V = {"h264": "AVC", "hevc": "HEVC", "mpeg4": "MPEG-4 Visual", "msmpeg4v3": "MPEG-4 Visual", "mpeg2video": "MPEG-2",
           "vp9": "VP9", "av1": "AV1", "vc1": "VC-1", "wmv3": "WMV"}
CODEC_A = {"ac3": "AC3", "eac3": "AC3+", "dts": "DTS", "aac": "AAC", "mp3": "MP3", "flac": "FLAC", "truehd": "TrueHD",
           "opus": "Opus", "vorbis": "Vorbis", "wmav2": "WMA"}
IDIOMAS = {"eng": "English", "spa": "Español", "fre": "Francés", "fra": "Francés", "ger": "Alemán", "deu": "Alemán",
           "ita": "Italiano", "por": "Portugués", "jpn": "Japonés", "kor": "Coreano", "chi": "Chino", "zho": "Chino",
           "rus": "Ruso", "hin": "Hindi", "ara": "Árabe", "tur": "Turco", "swe": "Sueco", "dut": "Holandés", "nld": "Holandés", "pol": "Polaco"}


def leer_tecnicos(ruta):
    """Lee formato, resolución, idiomas y subtítulos del archivo con ffprobe (parte de ffmpeg)."""
    exe = shutil.which("ffprobe") or shutil.which("ffprobe", path=str(AQUI))      # también junto a este programa
    if not exe:
        raise RuntimeError("No encuentro ffprobe. Instala ffmpeg (en Windows: winget install Gyan.FFmpeg) y reinicia el programa.")
    r = subprocess.run([exe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", ruta],
                       capture_output=True, text=True, timeout=90)
    info = json.loads(r.stdout or "{}")
    ss = info.get("streams", [])
    v = next((x for x in ss if x.get("codec_type") == "video" and not x.get("disposition", {}).get("attached_pic")), None)
    audios = [x for x in ss if x.get("codec_type") == "audio"]
    subs = [x for x in ss if x.get("codec_type") == "subtitle"]
    leng = lambda lst: ", ".join(dict.fromkeys(IDIOMAS.get((x.get("tags") or {}).get("language", "").lower()) for x in lst
                                               if IDIOMAS.get((x.get("tags") or {}).get("language", "").lower()))) or None
    a = audios[0] if audios else {}
    audio = CODEC_A.get(a.get("codec_name"), (a.get("codec_name") or "").upper()) or None
    if a.get("codec_name") == "dts" and "MA" in (a.get("profile") or ""):
        audio = "DTS-HD MA"
    fmt = info.get("format", {})
    out = {"formato_video": (CODEC_V.get(v.get("codec_name"), (v.get("codec_name") or "").upper()) or None) if v else None,
           "formato_audio": audio, "resolucion": f"{v['width']}x{v['height']}" if v and v.get("width") else None,
           "idiomas": leng(audios), "subtitulos": leng(subs),
           "tamano_mb": round(int(fmt["size"]) / 1e6) if fmt.get("size") else None}
    return {k: x for k, x in out.items() if x}


def completar_tecnicos():
    """Rellena SOLO los datos técnicos vacíos, leyendo los archivos de los discos conectados."""
    db = conectar(); n = ausentes = 0; errores = []
    cols = ("formato_video", "formato_audio", "resolucion", "idiomas", "subtitulos", "tamano_mb")
    for id_, ruta, tipo, *act in db.execute("SELECT id,ruta_archivo,tipo," + ",".join(cols) + " FROM titulos WHERE ruta_archivo IS NOT NULL").fetchall():
        if all(act[:5]) and (tipo == "serie" or act[5]):
            continue
        if not os.path.exists(ruta):
            ausentes += 1
            continue
        try:
            t = leer_tecnicos(ruta)
        except RuntimeError:
            raise
        except Exception as e:
            errores.append(f"{os.path.basename(ruta)}: {e}"); continue
        if tipo == "serie":
            t.pop("tamano_mb", None)
        nuevo = {k: v for k, v in t.items() if not dict(zip(cols, act))[k]}
        if nuevo:
            db.execute(f"UPDATE titulos SET {','.join(k + '=?' for k in nuevo)} WHERE id=?", [*nuevo.values(), id_]); db.commit(); n += 1
    db.commit(); db.close()
    if n:
        regenerar()
    return {"actualizados": n, "desconectados": ausentes, "errores": errores}


def pendientes_tmdb():
    """Fichas que todavía no tienen enlace de TMDB (por ejemplo, las que vienen de Ant Movie Catalog)."""
    return [{"id": r[0], "titulo": r[1] or r[2], "anio": r[3], "tipo": r[4]} for r in conectar().execute(
        "SELECT id,titulo_original,titulo_traducido,anio,tipo FROM titulos "
        "WHERE url IS NULL OR url NOT LIKE '%themoviedb.org/%' ORDER BY titulo_original")]


def proponer(ids):
    """Para cada ficha busca candidatos en TMDB. 'seguro' = mismo título y mismo año (se preselecciona)."""
    db = conectar(); out = []
    for id_ in ids:
        orig, trad, anio, tipo, url = db.execute("SELECT titulo_original,titulo_traducido,anio,tipo,url FROM titulos WHERE id=?", (int(id_),)).fetchone()
        cands, error = [], None
        m = re.search(r"imdb\.com/title/(tt\d+)", url or "")       # si la ficha ya trae un enlace de IMDb, es una coincidencia exacta
        if m:
            try:
                cands = resolver(m[1], tipo, anio)
            except Exception as e:
                error = str(e)
        for q in ([] if cands else dict.fromkeys(x for x in (orig, trad) if x)):
            try:
                cands = resolver(q, tipo, anio)
            except Exception as e:
                error = str(e); cands = []
            if cands:
                break
        nombres = {_norm(orig), _norm(trad)} - {""}
        seguro = (len(cands) == 1) if (m and cands) else bool(cands and anio and str(anio) == cands[0]["anio"] and ({_norm(cands[0]["titulo"]), _norm(cands[0]["original"])} & nombres))
        out.append({"id": id_, "cands": cands, "seguro": seguro, "error": error})
    return out


def aplicar_vinculos(items):
    """Guarda el enlace de TMDB y completa tráiler, colección y sinopsis SOLO si estaban vacíos."""
    db = conectar(); n = 0; errores = []
    for it in items:
        try:
            media, tid, id_ = it["media"], int(it["tmdb_id"]), int(it["id"])
            d = tmdb(f"{media}/{tid}", append_to_response="videos", include_video_language="es,en")
            trailer, coleccion, sinopsis, fondo = db.execute("SELECT trailer,coleccion,sinopsis,fondo FROM titulos WHERE id=?", (id_,)).fetchone()
            nuevo = {"url": f"https://www.themoviedb.org/{media}/{tid}", "tipo": "serie" if media == "tv" else "pelicula"}
            if not trailer:
                nuevo["trailer"] = elegir_trailer(d.get("videos", {}).get("results", []))
            if not coleccion:
                nuevo["coleccion"] = limpiar_coleccion((d.get("belongs_to_collection") or {}).get("name"))
            if not sinopsis:
                nuevo["sinopsis"] = d.get("overview") or None
            if not fondo and d.get("backdrop_path"):
                try:
                    nuevo["fondo"] = guardar_fondo(id_, d["backdrop_path"])
                except Exception:
                    pass
            nuevo = {k: v for k, v in nuevo.items() if v}
            db.execute(f"UPDATE titulos SET {','.join(k + '=?' for k in nuevo)} WHERE id=?", [*nuevo.values(), id_])
            db.commit()
            n += 1
        except Exception as e:
            errores.append(f"ficha {it.get('id')}: {e}")
    db.commit(); db.close()
    if n:
        regenerar()
    return {"aplicados": n, "errores": errores}


def similares_tmdb(id_):
    """Recomendaciones de TMDB para una ficha vinculada, marcando cuáles ya tienes en el catálogo."""
    db = conectar()
    (url,) = db.execute("SELECT url FROM titulos WHERE id=?", (int(id_),)).fetchone()
    m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
    if not m:
        raise ValueError("Esta ficha no está vinculada con TMDB. Usa Calidad → Vincúlalas con TMDB.")
    res = tmdb(f"{m[1]}/{m[2]}/recommendations")["results"]
    if len(res) < 8:
        res += tmdb(f"{m[1]}/{m[2]}/similar")["results"]
    tienen = {}
    for i, u, o, t, a in db.execute("SELECT id,url,titulo_original,titulo_traducido,anio FROM titulos"):
        mm = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", u or "")
        if mm:
            tienen[(mm[1], mm[2])] = i
        for n in {_norm(o), _norm(t)} - {""}:
            tienen.setdefault((n, a), i)
    out, vistos = [], set()
    for x in res:
        media = x.get("media_type") or m[1]
        it = item(media, x)
        if (media, it["id"]) in vistos or (media, str(it["id"])) == (m[1], m[2]):
            continue
        vistos.add((media, it["id"]))
        anio = int(it["anio"]) if str(it["anio"]).isdigit() else None
        en = tienen.get((media, str(it["id"]))) or tienen.get((_norm(it["titulo"]), anio)) or tienen.get((_norm(it["original"]), anio))
        out.append({**it, "en_catalogo": en})
        if len(out) == 12:
            break
    return out


def disponibilidad():
    """id -> 1 disponible, 0 disco desconectado, 2 archivo no encontrado (solo fichas con ruta)."""
    out = {}
    for id_, ruta in conectar().execute("SELECT id,ruta_archivo FROM titulos WHERE ruta_archivo IS NOT NULL"):
        if os.path.exists(ruta):
            out[id_] = 1
        else:
            unidad = ntpath.splitdrive(ruta)[0]
            out[id_] = 0 if unidad and not os.path.exists(unidad + "\\") else 2
    return out


def abrir_en_pc(id_, modo="archivo"):
    """Abre el video en el reproductor predeterminado (o su carpeta). Solo rutas guardadas en la ficha."""
    ruta, fuente = conectar().execute("SELECT ruta_archivo,fuente FROM titulos WHERE id=?", (int(id_),)).fetchone()
    if not ruta:
        raise ValueError("Esta ficha no tiene ruta de archivo.")
    destino = ruta if modo == "archivo" else os.path.dirname(ruta)
    if not os.path.exists(destino):
        unidad = ntpath.splitdrive(ruta)[0]
        if unidad and not os.path.exists(unidad + "\\"):
            raise ValueError(f"Conecta el disco {fuente or unidad} (no encuentro la unidad {unidad})")
        raise ValueError("El archivo ya no está en esa ruta (¿lo moviste o lo borraste?)")
    if sys.platform.startswith("win"):
        os.startfile(destino)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", destino])
    else:
        subprocess.Popen(["xdg-open", destino])
    return {"ok": True}


def maximo_temporada(txt):
    nums = [int(x) for x in re.findall(r"\d+", re.sub(r"complet[ae]", "", txt or "", flags=re.I))]
    return max(nums) if nums else None


def series_con_novedades(ids):
    """Series cuya cantidad de temporadas en TMDB supera la que tienes anotada."""
    db = conectar(); out = []
    for id_ in ids:
        t, temp, url = db.execute("SELECT COALESCE(titulo_original,titulo_traducido),temporadas,url FROM titulos WHERE id=?", (int(id_),)).fetchone()
        m = re.search(r"themoviedb\.org/tv/(\d+)", url or "")
        tiene = maximo_temporada(temp)
        if not m or tiene is None:
            continue
        try:
            d = tmdb(f"tv/{m[1]}")
        except Exception:
            continue
        if (d.get("number_of_seasons") or 0) > tiene:
            out.append({"id": id_, "titulo": t, "tienes": temp, "total": d["number_of_seasons"], "estado": d.get("status"),
                        "proxima": (d.get("next_episode_to_air") or {}).get("air_date")})
    return out


_EN = set("the and of to in is his her with for who an he she their that on by at from they this when after into".split())
_ES = set("el la de que y en un una los las del por con su se para al lo como más sus es".split())


def parece_ingles(t):
    pal = re.findall(r"[a-záéíóúñ']+", (t or "").lower())
    en, es = sum(w in _EN for w in pal), sum(w in _ES for w in pal)
    return en >= 3 and en > es


def sinopsis_mejor(media, tid):
    for lang in ("es-ES", "en-US"):
        try:
            t = tmdb(f"{media}/{tid}", language=lang).get("overview")
            if t:
                return t
        except Exception:
            pass
    return None


def sinopsis_a_espanol():
    """Reemplaza las sinopsis que están en inglés por la versión en español de TMDB (si existe)."""
    db = conectar(); n = sin_trad = sin_enlace = 0
    for id_, sin, url in db.execute("SELECT id,sinopsis,url FROM titulos WHERE sinopsis IS NOT NULL").fetchall():
        if not parece_ingles(sin):
            continue
        m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
        if not m:
            sin_enlace += 1
            continue
        nueva = None
        for lang in ("es-MX", "es-ES"):
            try:
                t = tmdb(f"{m[1]}/{m[2]}", language=lang).get("overview")
            except Exception:
                t = None
            if t and not parece_ingles(t):
                nueva = t
                break
        if nueva:
            db.execute("UPDATE titulos SET sinopsis=? WHERE id=?", (nueva, id_)); db.commit(); n += 1
        else:
            sin_trad += 1
    db.commit(); db.close()
    if n:
        regenerar()
    return {"actualizadas": n, "sin_traduccion": sin_trad, "sin_enlace": sin_enlace}


def _borrar_ids(db, ids):
    """Borra las fichas y devuelve los archivos de imagen a eliminar. Los archivos se borran DESPUÉS de
    confirmar el cambio en la base: si algo falla, no se pierde ninguna carátula."""
    archivos = []
    for i in range(0, len(ids), 500):
        parte = ids[i:i + 500]; marcas = ",".join("?" * len(parte))
        for p, f in db.execute(f"SELECT portada,fondo FROM titulos WHERE id IN ({marcas})", parte).fetchall():
            archivos += [x for x in (p, f) if x and x != "-"]
        db.execute(f"DELETE FROM titulos WHERE id IN ({marcas})", parte)
    db.execute("INSERT INTO titulos_fts(titulos_fts) VALUES('rebuild')")
    return archivos


def _quitar_archivos(lista):
    for p in lista:
        (CARPETA / p).unlink(missing_ok=True)


def mensaje_error(e):
    if isinstance(e, sqlite3.OperationalError) and "locked" in str(e):
        return "La base de datos está ocupada con otra tarea larga (por ejemplo, una descarga de imágenes). Espera a que termine e inténtalo de nuevo."
    return f"{type(e).__name__}: {e}"


def portadas_pendientes():
    """Fichas con enlace de TMDB cuya carátula falta (sin imagen o con el archivo perdido)."""
    return [i for i, p in conectar().execute("SELECT id,portada FROM titulos WHERE url LIKE '%themoviedb.org/%'")
            if not p or not (CARPETA / p).exists()]


def reparar_portadas(ids):
    db = conectar(); n = sin = 0; errores = []
    for id_ in ids:
        (url,) = db.execute("SELECT url FROM titulos WHERE id=?", (int(id_),)).fetchone()
        m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
        try:
            d = tmdb(f"{m[1]}/{m[2]}")
            if d.get("poster_path"):
                rel = f"portadas/{int(id_)}.jpg"
                guardar_portada(descargar("https://image.tmdb.org/t/p/w342" + d["poster_path"]), CARPETA / rel)
                db.execute("UPDATE titulos SET portada=? WHERE id=?", (rel, int(id_))); db.commit(); n += 1
            else:
                sin += 1
        except Exception as e:
            errores.append(f"ficha {id_}: {e}")
    db.close()
    if n:
        regenerar()
    return {"reparadas": n, "sin_imagen": sin, "errores": errores}


def lote(ids, accion, valor=None):
    """Acciones sobre varios títulos a la vez (selección múltiple del catálogo)."""
    ids = [int(i) for i in ids]
    if not ids:
        return {"n": 0}
    db = conectar(); borrados = []
    if accion == "eliminar":
        try:
            respaldar(prefijo="previo")
        except Exception:
            pass
        borrados = _borrar_ids(db, ids)
    else:
        for i in range(0, len(ids), 500):
            parte = ids[i:i + 500]; marcas = ",".join("?" * len(parte))
            if accion == "coleccion":
                db.execute(f"UPDATE titulos SET coleccion=? WHERE id IN ({marcas})", [(valor or "").strip() or None, *parte])
            elif accion == "estado":
                db.execute(f"UPDATE titulos SET estado=? WHERE id IN ({marcas})", [valor if valor in ("visto", "pendiente") else None, *parte])
            elif accion == "favorito":
                db.execute(f"UPDATE titulos SET favorito=? WHERE id IN ({marcas})", [1 if valor else 0, *parte])
            elif accion == "tipo" and valor in ("pelicula", "serie"):
                db.execute(f"UPDATE titulos SET tipo=? WHERE id IN ({marcas})", [valor, *parte])
            elif accion == "agregar_genero":
                g = (valor or "").strip()
                if not g:
                    raise ValueError("Escribe un género")
                for id_, gen in db.execute(f"SELECT id,generos FROM titulos WHERE id IN ({marcas})", parte).fetchall():
                    lista = [x.strip() for x in (gen or "").split(",") if x.strip()]
                    if g not in lista:
                        db.execute("UPDATE titulos SET generos=? WHERE id=?", (", ".join(lista + [g]), id_))
            else:
                raise ValueError("Acción desconocida")
    db.commit(); db.close()
    _quitar_archivos(borrados)
    regenerar()
    return {"n": len(ids)}


def eliminar_duplicados_exactos():
    """Fichas repetidas con la misma ruta y el mismo disco: conserva la primera y le pasa tus marcas."""
    try:
        respaldar(prefijo="previo")
    except Exception:
        pass
    db = conectar(); grupos = {}; sobrantes = []
    for id_, ruta, fuente in db.execute("SELECT id,ruta_archivo,fuente FROM titulos WHERE ruta_archivo IS NOT NULL ORDER BY id"):
        grupos.setdefault((ruta.lower(), fuente or ""), []).append(id_)
    for ids in grupos.values():
        if len(ids) > 1:
            conservar, resto = ids[0], ids[1:]
            marcas = ",".join("?" * len(resto))
            for col in ("estado", "favorito", "nota", "coleccion", "estrellas"):
                db.execute(f"UPDATE titulos SET {col}=COALESCE({col}, (SELECT {col} FROM titulos WHERE id IN ({marcas}) AND {col} IS NOT NULL LIMIT 1)) WHERE id=?", [*resto, conservar])
            sobrantes += resto
    borrados = _borrar_ids(db, sobrantes)
    db.commit(); db.close()
    _quitar_archivos(borrados)
    if sobrantes:
        regenerar()
    return {"eliminadas": len(sobrantes)}


def raiz_serie(ruta):
    d = os.path.dirname(ruta)
    return os.path.dirname(d) if re.fullmatch(r"(?i)(season|temporada|temp|s)[ ._-]*\d{1,2}.*", os.path.basename(d)) else d


def actualizar_temporadas():
    """Mira las carpetas de las series (en discos conectados) y sube las temporadas anotadas si hay más."""
    db = conectar(); cambios = []; ausentes = 0
    for id_, titulo, ruta, temp, url in db.execute("SELECT id,COALESCE(titulo_original,titulo_traducido),ruta_archivo,temporadas,url FROM titulos WHERE tipo='serie' AND ruta_archivo IS NOT NULL").fetchall():
        raiz = raiz_serie(ruta)
        if not os.path.isdir(raiz):
            ausentes += 1
            continue
        ss = temporadas_en(raiz)
        if not ss or max(ss) <= (maximo_temporada(temp) or 0):
            continue
        a, b = min(ss), max(ss)
        nuevo = f"S{a:02d}" if a == b else f"S{a:02d}-S{b:02d}"
        m = re.search(r"themoviedb\.org/tv/(\d+)", url or "")
        if m and a == 1:
            try:
                d = tmdb(f"tv/{m[1]}")
                if b >= (d.get("number_of_seasons") or 10**6) and d.get("status") in ("Ended", "Canceled"):
                    nuevo += " Completa"
            except Exception:
                pass
        db.execute("UPDATE titulos SET temporadas=? WHERE id=?", (nuevo, id_)); db.commit(); cambios.append(f"{titulo}: {temp or 'sin anotar'} → {nuevo}")
    db.commit(); db.close()
    if cambios:
        regenerar()
    return {"cambios": cambios, "desconectados": ausentes}


def importar_csv(texto):
    """Restaura fichas desde un CSV exportado: reemplaza las del mismo id y agrega las que faltan."""
    try:
        respaldar(prefijo="previo")
    except Exception:
        pass
    filas = list(csv.DictReader(io.StringIO(texto.lstrip("\ufeff"))))
    if not filas or "id" not in filas[0]:
        raise ValueError("Este CSV no parece exportado por el catálogo (falta la columna id).")
    db = conectar()
    tipos = {r[1]: r[2].upper() for r in db.execute("PRAGMA table_info(titulos)")}
    cols = [c for c in filas[0] if c in tipos]
    for f in filas:
        vals = []
        for c in cols:
            v = f[c]
            if v == "":
                v = None
            elif "INT" in tipos[c]:
                v = int(float(v))
            elif tipos[c] == "REAL":
                v = float(v)
            vals.append(v)
        db.execute(f"INSERT OR REPLACE INTO titulos ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    db.execute("INSERT INTO titulos_fts(titulos_fts) VALUES('rebuild')")
    db.commit(); db.close()
    regenerar()
    return {"importadas": len(filas)}


def abrir_carpeta(ruta):
    if sys.platform.startswith("win"):
        os.startfile(ruta)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


def exportar_web(whatsapp=""):
    """Crea catalogo/sitio/: una web de solo lectura (sin rutas ni datos personales) para subir a internet."""
    import generar_web
    salida = CARPETA / "sitio"
    if salida.exists():
        shutil.rmtree(salida)
    (salida / "portadas").mkdir(parents=True)
    (salida / "fondos").mkdir()
    generar_web.generar(CARPETA, publico=True, whatsapp=whatsapp, salida=salida)
    for p in (CARPETA / "portadas").glob("*.jpg"):
        shutil.copy2(p, salida / "portadas" / p.name)
    usados = set(re.findall(r"fondos/\d+\.jpg", (salida / "index.html").read_text(encoding="utf-8")))      # solo las imágenes que la web usa
    for nombre in usados:
        if (CARPETA / nombre).exists():
            shutil.copy2(CARPETA / nombre, salida / nombre)
    total = sum(f.stat().st_size for f in salida.rglob("*") if f.is_file())
    return {"carpeta": str(salida), "titulos": conectar().execute("SELECT COUNT(*) FROM titulos").fetchone()[0], "mb": round(total / 1e6, 1)}


def guardar_config(**cambios):
    """Cambia claves de config.json sin tocar las demás (valor None = quitar la clave)."""
    cfg = AQUI / "config.json"
    d = json.loads(cfg.read_text()) if cfg.exists() else {}
    for k, v in cambios.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    cfg.write_text(json.dumps(d, indent=2))


def sugerencias_nube():
    """Carpetas de OneDrive, Google Drive o Dropbox que existan en este computador."""
    home = Path.home(); cand = []
    posibles = [("OneDrive", os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")), ("OneDrive", home / "OneDrive"),
                ("Google Drive", home / "Google Drive"), ("Dropbox", home / "Dropbox")]
    posibles += [("Google Drive", f"{l}:\\\\{n}") for l in "GHIJKLMNOPQRSTUVWXYZ" for n in ("Mi unidad", "My Drive")]
    for nombre, ruta in posibles:
        if ruta and Path(ruta).is_dir():
            sug = str(Path(ruta) / "RespaldoCatalogo")
            if all(sug != c["ruta"] for c in cand):
                cand.append({"nombre": nombre, "ruta": sug})
    return cand


def info_respaldos():
    carpeta = CARPETA / "respaldos"
    return {"extra": RESPALDO_EXTRA, "sugerencias": sugerencias_nube(),
            "locales": sorted(p.name for p in carpeta.glob("*.db"))[-5:] if carpeta.exists() else []}


def configurar_respaldo(ruta):
    global RESPALDO_EXTRA
    ruta = (ruta or "").strip().strip('"')
    if not ruta:
        guardar_config(carpeta_respaldo=None); RESPALDO_EXTRA = None
        return {"extra": None}
    p = Path(ruta)
    try:
        p.mkdir(parents=True, exist_ok=True)
        prueba = p / ".prueba"; prueba.write_text("ok"); prueba.unlink()      # comprueba que se puede escribir ahí
    except OSError as e:
        raise ValueError(f"No puedo escribir en esa carpeta ({e.strerror or e}). Revisa la ruta.")
    guardar_config(carpeta_respaldo=ruta); RESPALDO_EXTRA = ruta
    return {"extra": ruta, "archivo": respaldar()}


def guardar_fondo(lid, ruta_tmdb):
    """Descarga la imagen ancha (backdrop) de TMDB, reducida, para los destacados del inicio."""
    if not ruta_tmdb:
        return None
    (CARPETA / "fondos").mkdir(exist_ok=True)
    rel = f"fondos/{int(lid)}.jpg"
    guardar_portada(descargar("https://image.tmdb.org/t/p/w1280" + ruta_tmdb), CARPETA / rel, ancho=1280, alto=720)
    return rel


def fondos_pendientes():
    return [r[0] for r in conectar().execute("SELECT id FROM titulos WHERE url LIKE '%themoviedb.org/%' AND fondo IS NULL AND sinopsis IS NOT NULL ORDER BY COALESCE(calificacion,0) DESC, id LIMIT 150")]


def descargar_fondos(ids):
    db = conectar(); n = sin = 0; errores = []
    for id_ in ids:
        (url,) = db.execute("SELECT url FROM titulos WHERE id=?", (int(id_),)).fetchone()
        m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
        try:
            rel = guardar_fondo(id_, tmdb(f"{m[1]}/{m[2]}").get("backdrop_path"))
            db.execute("UPDATE titulos SET fondo=? WHERE id=?", (rel or "-", int(id_)))      # '-' = TMDB no tiene imagen
            db.commit()      # libera la base en cada ficha: así no bloquea lo demás durante tareas largas
            n, sin = (n + 1, sin) if rel else (n, sin + 1)
        except Exception as e:
            errores.append(f"ficha {id_}: {e}")
    db.commit(); db.close()
    if n:
        regenerar()
    return {"descargados": n, "sin_imagen": sin, "errores": errores}


def estados():
    db = conectar()
    return {r[0]: [r[1], r[2] or 0, r[3], r[4] or 0] for r in db.execute(
        "SELECT id,estado,favorito,nota,estrellas FROM titulos WHERE estado IS NOT NULL OR favorito=1 OR nota IS NOT NULL OR estrellas IS NOT NULL")}


def marcar(id_, estado=None, favorito=None, estrellas=None):
    db = conectar()
    if estado is not None:
        db.execute("UPDATE titulos SET estado=? WHERE id=?", (estado if estado in ("visto", "pendiente") else None, int(id_)))
    if favorito is not None:
        db.execute("UPDATE titulos SET favorito=? WHERE id=?", (1 if favorito else 0, int(id_)))
    if estrellas is not None:
        n = int(estrellas)
        db.execute("UPDATE titulos SET estrellas=? WHERE id=?", (n if 1 <= n <= 5 else None, int(id_)))
    db.commit(); db.close()


def borrar(id_):
    """Elimina la ficha (no toca el archivo de video). Así el disco se puede volver a escanear."""
    db = conectar()
    p = db.execute("SELECT portada FROM titulos WHERE id=?", (int(id_),)).fetchone()
    db.execute("DELETE FROM titulos WHERE id=?", (int(id_),))
    db.execute("INSERT INTO titulos_fts(titulos_fts) VALUES('rebuild')")
    db.commit(); db.close()
    if p and p[0]:
        (CARPETA / p[0]).unlink(missing_ok=True)
    regenerar()


def ip_local():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "IP-DE-ESTE-PC"
    finally:
        s.close()


def tiene_no_latino(s):
    return any(c.isalpha() and not unicodedata.name(c, "").startswith("LATIN") for c in (s or ""))


_NOMBRES = {}


def nombre_latino(p):
    """Nombre de una persona en alfabeto latino (usa los 'también conocido como' de TMDB)."""
    n = p["name"]
    if es_latino(n) or not p.get("id"):
        return n
    if p["id"] not in _NOMBRES:
        _NOMBRES[p["id"]] = n
        try:
            for alt in tmdb(f"person/{p['id']}", language="en-US").get("also_known_as", []):
                if es_latino(alt):
                    _NOMBRES[p["id"]] = alt
                    break
        except Exception:
            pass
    return _NOMBRES[p["id"]]


def sinopsis_en(media, tid):
    try:
        return tmdb(f"{media}/{tid}", language="en-US").get("overview") or None
    except Exception:
        return None


def refrescar(id_, regen=True):
    """Vuelve a consultar TMDB y corrige solo lo que está en otro alfabeto, la sinopsis vacía
    o una valoración de 10 dudosa. No toca lo que ya está en alfabeto latino (por si lo editaste)."""
    db = conectar()
    tipo, orig, trad, sin, dire, guion, mus, rep, cal, url = db.execute(
        "SELECT tipo,titulo_original,titulo_traducido,sinopsis,director,guion,musica,reparto,calificacion,url "
        "FROM titulos WHERE id=?", (int(id_),)).fetchone()
    m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url or "")
    if not m:
        raise ValueError("Esta ficha no viene de TMDB, así que no hay de dónde actualizarla. Edítala a mano.")
    media, tid = m[1], m[2]
    d = tmdb(f"{media}/{tid}", append_to_response="credits")
    en = tmdb(f"{media}/{tid}", language="en-US")
    campo = "name" if media == "tv" else "title"
    eq, cast = d.get("credits", {}).get("crew", []), d.get("credits", {}).get("cast", [])
    unir = lambda xs: ", ".join(dict.fromkeys(x for x in xs if x)) or None
    rol = lambda *r: unir(nombre_latino(x) for x in eq if x.get("job") in r)
    nuevo = {}
    if not es_latino(orig) and not es_latino(trad):
        nuevo["titulo_traducido"] = next((t for t in (d.get(campo), en.get(campo)) if es_latino(t) and t != orig), None)
    if not sin:
        nuevo["sinopsis"] = d.get("overview") or en.get("overview") or None
    if tiene_no_latino(dire):
        nuevo["director"] = (unir(nombre_latino(x) for x in d.get("created_by", [])) if media == "tv" else None) or rol("Director")
    if tiene_no_latino(guion):
        nuevo["guion"] = rol("Screenplay", "Writer", "Story")
    if tiene_no_latino(mus):
        nuevo["musica"] = rol("Original Music Composer")
    if tiene_no_latino(rep):
        nuevo["reparto"] = unir(nombre_latino(x) for x in cast[:12])
    if cal is not None and cal >= 10:
        nuevo["calificacion"] = round(d.get("vote_average") or 0, 1) if (d.get("vote_count") or 0) >= 10 else None
    actual = {"titulo_traducido": trad, "sinopsis": sin, "director": dire, "guion": guion, "musica": mus, "reparto": rep, "calificacion": cal}
    nuevo = {k: v for k, v in nuevo.items() if v != actual[k] and (v is not None or k == "calificacion")}
    if nuevo:
        db.execute(f"UPDATE titulos SET {','.join(k + '=?' for k in nuevo)} WHERE id=?", [*nuevo.values(), int(id_)])
        db.execute("INSERT INTO titulos_fts(titulos_fts) VALUES('rebuild')")
        db.commit()
    db.close()
    if nuevo and regen:
        regenerar()
    return bool(nuevo)


def refrescar_todo():
    """Revisa todas las fichas con enlace de TMDB y corrige las que lo necesiten."""
    db = conectar()
    filas = db.execute("SELECT id,titulo_original,titulo_traducido,director,guion,musica,reparto,calificacion "
                       "FROM titulos WHERE url LIKE '%themoviedb.org/%'").fetchall()
    db.close()
    n, errores = 0, []
    for id_, orig, trad, *resto in filas:
        cal = resto[-1]
        if (not es_latino(orig) and not es_latino(trad)) or any(tiene_no_latino(x) for x in resto[:-1]) or cal == 10:
            try:
                n += refrescar(id_, regen=False)
            except Exception as e:
                errores.append(f"{orig}: {e}")
    if n:
        regenerar()
    return {"corregidos": n, "errores": errores}


def limpiar_colecciones_solitarias():
    """Quita la colección a los títulos que son los únicos de su colección."""
    db = conectar()
    n = db.execute("UPDATE titulos SET coleccion=NULL WHERE coleccion IN "
                   "(SELECT coleccion FROM titulos WHERE coleccion IS NOT NULL GROUP BY coleccion HAVING COUNT(*)=1)").rowcount
    db.commit(); db.close(); regenerar()
    return {"quitadas": n}


def aplicar_colecciones(grupos):
    db = conectar(); n = 0
    for g in grupos:
        for i in g["ids"]:
            db.execute("UPDATE titulos SET coleccion=? WHERE id=?", (g["nombre"].strip(), int(i))); n += 1
    db.commit(); db.close(); regenerar()
    return {"aplicados": n}


def guardar(it, fuente):
    with _GUARDAR:      # una grabación a la vez: evita duplicados si hay dos escaneos en paralelo
        return _guardar(it, fuente)


def _guardar(it, fuente):
    fuente = fuente.strip().upper()
    media, tv = it["media"], it["media"] == "tv"
    db = conectar()
    if (it.get("ruta") and db.execute("SELECT 1 FROM titulos WHERE lower(ruta_archivo)=lower(?) AND COALESCE(fuente,'')=?", (it["ruta"], fuente)).fetchone()) or \
            (not it.get("ruta") and db.execute("SELECT 1 FROM titulos WHERE url=? AND COALESCE(fuente,'')=?", (f"https://www.themoviedb.org/{it['media']}/{it['id']}", fuente)).fetchone()):
        return None
    d = tmdb(f"{media}/{it['id']}", append_to_response="credits,videos", include_video_language="es,en")
    eq = d.get("credits", {}).get("crew", [])
    unir = lambda xs: ", ".join(dict.fromkeys(x for x in xs if x)) or None
    por_rol = lambda *roles: unir(nombre_latino(x) for x in eq if x.get("job") in roles)
    gen = []
    for g in d.get("genres", []):
        for x in (GEN.get(g["name"], g["name"]) or "").split(", "):
            if x and x not in gen:
                gen.append(x)
    orig = d.get("original_name" if tv else "original_title")
    trad = d.get("name" if tv else "title")
    if not es_latino(orig) and not es_latino(trad):      # título solo en chino/coreano/hindi...: pide el inglés
        try:
            en = tmdb(f"{media}/{it['id']}", language="en-US").get("name" if tv else "title")
            if es_latino(en):
                trad = en
        except Exception:
            pass
    fecha = (d.get("first_air_date" if tv else "release_date") or "")[:4]
    temp = None
    if tv and it.get("temporadas"):
        a, b = min(it["temporadas"]), max(it["temporadas"])
        temp = (f"S{a:02d}" if a == b else f"S{a:02d}-S{b:02d}")
        temp += " Completa" if a == 1 and b >= d.get("number_of_seasons", 10**6) and d.get("status") in ("Ended", "Canceled") else ""
    f = {"tipo": "serie" if tv else "pelicula", "origen": "escaneo", "titulo_original": orig,
         "titulo_traducido": trad if trad != orig else None, "anio": int(fecha) if fecha else None,
         "duracion_min": ((d.get("episode_run_time") or [None])[0] if tv else d.get("runtime")) or None,
         "calificacion": (round(d.get("vote_average") or 0, 1) if (d.get("vote_count") or 0) >= 10 else None) or None,
         "director": unir(nombre_latino(x) for x in d.get("created_by", [])) if tv else por_rol("Director"),
         "productora": unir(x["name"] for x in d.get("production_companies", [])),
         "guion": por_rol("Screenplay", "Writer", "Story"), "musica": por_rol("Original Music Composer"),
         "pais": unir(x["name"] for x in d.get("production_countries", [])), "generos": ", ".join(gen) or None,
         "reparto": unir(nombre_latino(x) for x in d.get("credits", {}).get("cast", [])[:12]),
         "url": f"https://www.themoviedb.org/{media}/{it['id']}", "sinopsis": d.get("overview") or sinopsis_mejor(media, it["id"]),
         "medio": "Disco Duro", "tipo_medio": fuente, "fuente": fuente, "ruta_archivo": it.get("ruta"),
         "temporadas": temp, "trailer": elegir_trailer(d.get("videos", {}).get("results", [])),
         "coleccion": limpiar_coleccion((d.get("belongs_to_collection") or {}).get("name")), "fecha_agregado": datetime.date.today().isoformat(), "marcado": 0}
    if it.get("ruta") and os.path.exists(it["ruta"]):
        try:
            t = leer_tecnicos(it["ruta"])
            if tv:
                t.pop("tamano_mb", None)
            f.update(t)
        except Exception:
            pass          # sin ffprobe o archivo ilegible: se guarda igual, sin datos técnicos
    cur = db.execute(f"INSERT INTO titulos ({','.join(f)}) VALUES ({','.join('?' * len(f))})", list(f.values()))
    lid = cur.lastrowid
    db.commit()      # libera la base de inmediato: lo que sigue (descargar imágenes) es lento
    con_portada = False
    if d.get("poster_path"):
        try:
            rel = f"portadas/{lid}.jpg"
            guardar_portada(descargar("https://image.tmdb.org/t/p/w342" + d["poster_path"]), CARPETA / rel)
            db.execute("UPDATE titulos SET portada=? WHERE id=?", (rel, lid))
            con_portada = True
        except Exception as e:
            print("Sin carátula:", orig, e)
    try:
        fondo = guardar_fondo(lid, d.get("backdrop_path"))
        if fondo:
            db.execute("UPDATE titulos SET fondo=? WHERE id=?", (fondo, lid))
    except Exception as e:
        print("Sin imagen de fondo:", orig, e)
    db.execute("INSERT INTO titulos_fts(rowid,titulo_original,titulo_traducido,director,reparto,generos,sinopsis) "
               "VALUES (?,?,?,?,?,?,?)", (lid, orig, f["titulo_traducido"], f["director"], f["reparto"], f["generos"], f["sinopsis"]))
    db.commit(); db.close()
    return orig + ("" if con_portada else " (sin carátula)")


_REGEN = threading.Lock()


def regenerar():
    import generar_web
    with _REGEN:
        generar_web.generar(CARPETA)


# ---------- servidor ----------
PAGINA = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agregar disco</title><style>
body{margin:0;background:#141414;color:#f2f2f2;font:15px system-ui,sans-serif;padding:20px 4vw 60px}a{color:#6ab0ff}
input,select,button{background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;font-size:14px}
button{cursor:pointer}#ruta{width:min(420px,90%)}.fila{border-top:1px solid #2a2a2a;padding:12px 0}.fila.rev{background:#2b2410}
.fila input[type=text]{width:min(360px,70%)}.op{display:flex;gap:10px;margin:6px 0;cursor:pointer}.op img{width:46px;height:69px;object-fit:cover;background:#333;border-radius:3px}
small,.mu{color:#9a9a9a}#gu{background:#e50914;border-color:#e50914;margin-top:16px}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}@keyframes barra{0%{left:-35%}100%{left:100%}}
</style>
<h1>Agregar disco</h1><p><a href="/">← Volver al catálogo</a></p>
<form id="f">Carpeta <input id="ruta" placeholder="E:\Peliculas" required> Disco <input id="fu" size="6" placeholder="E02" required>
Contiene <select id="tipo"><option value="pelicula">Películas</option><option value="serie">Series</option></select> <button>Escanear</button></form>
<p id="st" class="mu"></p><div id="lista"></div><button id="gu" hidden>Guardar seleccionados</button>
<hr style="border:0;border-top:1px solid #2a2a2a;margin:30px 0"><h3>Mantenimiento</h3>
<p class="mu">Si hay títulos, directores o reparto en chino, ruso, coreano, etc., esto los consulta de nuevo en TMDB y los pasa a alfabeto latino. También completa sinopsis vacías y revisa valoraciones de 10 dudosas. No toca lo que ya esté en alfabeto latino.</p>
<button id="rf">Corregir títulos y nombres desde TMDB</button> <span id="rm" class="mu"></span>
<p class="mu">Lee formato, idiomas y subtítulos de los archivos de video (necesita ffmpeg instalado y el disco conectado; solo completa lo que está vacío).</p>
<button id="tc">Leer datos técnicos de los archivos</button> <span id="tm2" class="mu"></span>
<p class="mu">Si hay sinopsis en inglés, las cambia por la versión en español de TMDB (si existe; las fichas sin enlace de TMDB se saltan).</p>
<button id="se">Poner sinopsis en español</button> <span id="sm" class="mu"></span>
<p class="mu">Si descargaste temporadas nuevas de una serie, esto las detecta en sus carpetas (solo discos conectados) y actualiza las temporadas anotadas.</p>
<button id="tt">Actualizar temporadas desde las carpetas</button> <span id="tt2" class="mu"></span>
<p class="mu">Descarga las imágenes grandes del banner de destacados del inicio: las 150 mejor valoradas cada vez que lo pulses (solo fichas vinculadas con TMDB). Tarda unos minutos.</p>
<button id="fd">Descargar imágenes de destacados</button> <span id="fd2" class="mu"></span>
<p class="mu">Si hay tarjetas sin carátula (fichas vinculadas con TMDB), vuelve a descargarlas.</p>
<button id="pf">Recuperar carátulas que faltan</button> <span id="pf2" class="mu"></span>
<hr style="border:0;border-top:1px solid #2a2a2a;margin:30px 0"><h3>Agregar un título a mano</h3>
<p class="mu">Para lo que el escáner no reconoció o no encontró. Busca en TMDB, elige la correcta y se agrega con el disco y la ruta que indiques (la ruta es opcional).</p>
<input type="text" id="mq" size="30" placeholder="Título, o URL de TMDB / IMDb"> <input type="text" id="my" size="5" placeholder="Año"> <select id="mt"><option value="pelicula">Película</option><option value="serie">Serie</option></select> <button id="mb">Buscar</button>
<div id="mr"></div><div id="ma" hidden>Disco <input type="text" id="mf" size="6" placeholder="E02"> Ruta del archivo <input type="text" id="mp" size="42" placeholder="opcional"> <button id="mg">Agregar</button></div><p id="mm" class="mu"></p>
<p class="mu">Fichas antiguas sin enlace a TMDB: <a href="/vincular">vincularlas con TMDB</a> (tráiler, colección, sinopsis).</p>
<p class="mu"><small>Datos de TMDB. Este producto usa la API de TMDB pero no está avalado ni certificado por TMDB.</small></p>
<script>
(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const $=id=>document.getElementById(id);let filas=[];
const norm=s=>(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(u,b){const r=await fetch(u,b?{method:'POST',body:JSON.stringify(b)}:{});const j=await r.json();if(j.error)throw new Error(j.error);return j}
$('f').onsubmit=async e=>{e.preventDefault();$('st').textContent='Escaneando…';$('gu').hidden=true;
 try{filas=await api('/api/escanear',{ruta:$('ruta').value,tipo:$('tipo').value,fuente:$('fu').value})}catch(x){$('st').textContent=x.message;return}
 $('lista').innerHTML=filas.map((f,i)=>`<div class="fila" id="fila${i}"><b>${esc(f.nombre)}</b>${f.temporadas?` <small>temporadas: ${f.temporadas.join(', ')||'?'}</small>`:''}<br>
  <input type="text" id="q${i}" value="${esc(f.titulo+(f.anio?' ':''))}${f.anio||''}" placeholder="título, o URL de TMDB / IMDb / FilmAffinity">
  <button onclick="buscar(${i})">Buscar</button> <label><input type="checkbox" id="o${i}"> omitir</label><div id="r${i}" class="mu">Buscando…</div></div>`).join('');
 $('st').textContent=filas.length?filas.length+' elementos nuevos.':'0 elementos nuevos: ya estaban en el catálogo con ese disco, pesan menos de 20 MB o tienen una extensión no reconocida. Si buscas un solo archivo, pega la ruta completa del archivo en vez de la carpeta.';
 for(let i=0;i<filas.length;i++)await buscar(i);
 $('st').textContent='Revisa las filas en amarillo (varias coincidencias o ninguna) y pulsa Guardar.';$('gu').hidden=!filas.length};
async function buscar(i){const f=filas[i];let q=$('q'+i).value.trim(),anio=f.anio||'';
 const m=q.match(/^(.*\S)\s+((?:19|20)\d\d)$/);if(m&&!/^https?:/.test(q)){q=m[1];anio=m[2]}
 try{f.res=await api('/api/buscar?'+new URLSearchParams({q,tipo:$('tipo').value,anio}))}catch(x){f.res=[];f.err=x.message}
 f.sel=f.res.length?0:-1;const r0=f.res[0];
 const exacto=r0&&f.res.length&&(norm(r0.titulo)==norm(q)||norm(r0.original)==norm(q))&&(!anio||r0.anio==anio);
 $('fila'+i).classList.toggle('rev',!exacto);
 $('r'+i).innerHTML=f.res.length?f.res.map((x,j)=>`<label class="op"><input type="radio" name="s${i}" ${j==0?'checked':''} onchange="filas[${i}].sel=${j}">
  <img src="${x.poster?'https://image.tmdb.org/t/p/w92'+x.poster:''}" alt=""><span><b>${esc(x.titulo)}</b> (${esc(x.anio)}) · ${x.media=='tv'?'serie':'película'}${x.original&&x.original!=x.titulo?' · '+esc(x.original):''}<br><small>${esc(x.resumen)}</small></span></label>`).join('')
  :`<span style="color:#ff8a8a">${esc(f.err||'Sin resultados.')} Corrige el título o pega una URL.</span>`}
$('rf').onclick=async()=>{$('rm').textContent='Consultando TMDB… puede tardar un poco';try{const r=await api('/api/refrescar',{});$('rm').innerHTML=`Listo: ${r.corregidos} fichas corregidas${r.errores.length?'; sin poder corregir: '+esc(r.errores.join('; ')):''}. <a href="/">Ver catálogo</a>`}catch(x){$('rm').textContent=x.message}};
$('pf').onclick=async()=>{const s=$('pf2');s.textContent='Buscando carátulas faltantes…';try{const ids=await api('/api/portadas_pendientes');let n=0,sin=0,err=[];for(let i=0;i<ids.length;i+=8){s.textContent=`Descargando ${Math.min(i+8,ids.length)} de ${ids.length}…`;const r=await api('/api/portadas',{ids:ids.slice(i,i+8)});n+=r.reparadas;sin+=r.sin_imagen;err.push(...r.errores)}
 s.innerHTML=ids.length?`Listo: ${n} carátulas recuperadas, ${sin} sin imagen en TMDB${err.length?'; con error: '+esc(err.slice(0,3).join('; ')):''}. <a href="/">Ver catálogo</a>`:'No falta ninguna carátula en las fichas vinculadas con TMDB.'}catch(x){s.textContent=x.message}};
$('fd').onclick=async()=>{const s=$('fd2');s.textContent='Buscando fichas…';try{const ids=await api('/api/fondos_pendientes');let n=0,sin=0,err=[];for(let i=0;i<ids.length;i+=8){s.textContent=`Descargando ${Math.min(i+8,ids.length)} de ${ids.length}…`;const r=await api('/api/fondos',{ids:ids.slice(i,i+8)});n+=r.descargados;sin+=r.sin_imagen;err.push(...r.errores)}
 s.innerHTML=ids.length?`Listo: ${n} imágenes descargadas, ${sin} sin imagen en TMDB${err.length?'; con error: '+esc(err.slice(0,3).join('; ')):''}. <a href="/">Ver catálogo</a>`:'No quedan fichas pendientes (todas las vinculadas con TMDB y con sinopsis ya tienen imagen).'}catch(x){s.textContent=x.message}};
$('tt').onclick=async()=>{$('tt2').textContent='Revisando carpetas…';try{const r=await api('/api/temporadas_carpetas',{});$('tt2').innerHTML=r.cambios.length?`${r.cambios.length} series actualizadas:<br>`+r.cambios.slice(0,15).map(esc).join('<br>')+(r.cambios.length>15?'<br>…':''):`Sin cambios (${r.desconectados} series en discos no conectados).`}catch(x){$('tt2').textContent=x.message}};
$('se').onclick=async()=>{$('sm').textContent='Consultando TMDB… puede tardar';try{const r=await api('/api/sinopsis_es',{});$('sm').innerHTML=`Listo: ${r.actualizadas} sinopsis cambiadas; ${r.sin_traduccion} sin versión en español en TMDB; ${r.sin_enlace} fichas sin enlace de TMDB. <a href="/">Ver catálogo</a>`}catch(x){$('sm').textContent=x.message}};
let MC=[];$('mb').onclick=async()=>{try{MC=await api('/api/buscar?'+new URLSearchParams({q:$('mq').value.trim(),tipo:$('mt').value,anio:$('my').value.trim()}));
 $('mr').innerHTML=MC.length?MC.map((x,j)=>`<label class="op"><input type="radio" name="mc" value="${j}" ${j==0?'checked':''}><img src="${x.poster?'https://image.tmdb.org/t/p/w92'+x.poster:''}" alt=""><span><b>${esc(x.titulo)}</b> (${esc(x.anio)}) · ${x.media=='tv'?'serie':'película'}<br><small>${esc(x.resumen)}</small></span></label>`).join(''):'Sin resultados.';$('ma').hidden=!MC.length}catch(x){$('mr').textContent=x.message}};
$('mg').onclick=async()=>{const j=+(document.querySelector('input[name=mc]:checked')||{}).value;if(isNaN(j))return;const x=MC[j],fu=$('mf').value.trim();if(!fu){$('mm').textContent='Escribe el disco (ej: E02).';return}
 try{const r=await api('/api/guardar',{fuente:fu,items:[{...x,ruta:$('mp').value.trim().replace(/^"|"$/g,'')||null}]});$('mm').innerHTML=r.guardados.length?`Agregada: ${esc(r.guardados[0])}. <a href="/">Ver catálogo</a>`:'No se agregó: ya existía con esa ruta y ese disco.'}catch(e){$('mm').textContent=e.message}};
$('tc').onclick=async()=>{$('tm2').textContent='Leyendo archivos… puede tardar';try{const r=await api('/api/tecnicos',{});$('tm2').innerHTML=`Listo: ${r.actualizados} fichas completadas; ${r.desconectados} en discos no conectados${r.errores.length?'; con error: '+esc(r.errores.slice(0,5).join('; ')):''}. <a href="/">Ver catálogo</a>`}catch(x){$('tm2').textContent=x.message}};
$('gu').onclick=async()=>{const items=filas.map((f,i)=>({f,i})).filter(({f,i})=>f.sel>=0&&!$('o'+i).checked).map(({f})=>({...f.res[f.sel],ruta:f.ruta,temporadas:f.temporadas}));
 if(!items.length)return;$('gu').disabled=true;$('st').textContent='Guardando '+items.length+' títulos (descargando carátulas)…';
 try{const r=await api('/api/guardar',{fuente:$('fu').value.trim(),items});
  $('st').innerHTML=`Listo: ${r.guardados.length} agregados${r.guardados.some(n=>n.endsWith('(sin carátula)'))?' (sin carátula: '+esc(r.guardados.filter(n=>n.endsWith('(sin carátula)')).join(', '))+'; súbela con ✎ Editar)':''}${r.errores.length?', '+r.errores.length+' con error: '+esc(r.errores.join('; ')):''}. <a href="/">Ver catálogo</a>`;$('lista').innerHTML=''}
 catch(x){$('st').textContent=x.message}$('gu').disabled=false;$('gu').hidden=true};
</script></html>"""
PAGINA_COL = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Colecciones</title><style>body{margin:0;background:#141414;color:#f2f2f2;font:15px system-ui,sans-serif;padding:20px 4vw 60px}a{color:#6ab0ff}.mu{color:#9a9a9a}
input[type=text],button{background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;font-size:14px}.f{border-top:1px solid #2a2a2a;padding:12px 0}label{display:block;margin:4px 0;cursor:pointer}#ap{background:#e50914;border-color:#e50914;cursor:pointer}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}@keyframes barra{0%{left:-35%}100%{left:100%}}
</style>
<h1>Sugerir colecciones</h1><p><a href="/">← Volver al catálogo</a></p>
<p class="mu">Agrupé películas y series cuyo título empieza con la misma palabra. Desmarca lo que no corresponda, cambia el nombre o bórralo para omitir el grupo.</p>
<p><button id="lc">Quitar las colecciones que tienen un solo título</button> <span id="lm" class="mu"></span></p>
<div id="l">Buscando…</div><button id="ap" hidden>Aplicar</button><p id="st"></p>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
let G=[];const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
fetch('/api/sugerir').then(r=>r.json()).then(g=>{G=g;document.getElementById('l').innerHTML=g.length?g.map((x,i)=>`<div class="f"><input type="text" id="n${i}" value="${esc(x.nombre)}">${x.items.map(t=>`<label><input type="checkbox" data-g="${i}" value="${t.id}" checked> ${esc(t.titulo)} (${t.anio||'?'}) · ${t.tipo}</label>`).join('')}</div>`).join(''):'No encontré grupos nuevos.';document.getElementById('ap').hidden=!g.length});
document.getElementById('lc').onclick=async()=>{if(!confirm('¿Quitar la colección a los títulos que están solos en ella?'))return;const r=await(await fetch('/api/limpiar_colecciones',{method:'POST',body:'{}'})).json();document.getElementById('lm').textContent=r.error||('Listo: '+r.quitadas+' títulos sin colección.')};
document.getElementById('ap').onclick=async()=>{const grupos=G.map((x,i)=>({nombre:document.getElementById('n'+i).value.trim(),ids:[...document.querySelectorAll(`input[data-g="${i}"]:checked`)].map(c=>+c.value)})).filter(g=>g.nombre&&g.ids.length>1);
 const r=await(await fetch('/api/colecciones',{method:'POST',body:JSON.stringify({grupos})})).json();document.getElementById('st').innerHTML=r.error?esc(r.error):`Listo: ${r.aplicados} títulos en ${grupos.length} colecciones. <a href="/">Ver catálogo</a>`};
</script></html>"""
PAGINA_CAL = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calidad de datos</title><style>body{margin:0;background:#141414;color:#f2f2f2;font:15px system-ui,sans-serif;padding:20px 4vw 60px}a{color:#6ab0ff}.mu{color:#9a9a9a}
.f{padding:3px 0}.g{border:1px solid #333;border-radius:8px;padding:8px 12px;margin:8px 0}h3{margin:26px 0 6px}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}@keyframes barra{0%{left:-35%}100%{left:100%}}</style>
<h1>Revisión de calidad de datos</h1><p><a href="/">← Volver al catálogo</a></p>
<p class="mu">¿Fichas antiguas sin enlace a TMDB? <a href="/vincular">Vincúlalas con TMDB</a> para completar tráiler, colección y sinopsis.</p><div id="r">Revisando…</div>
<h3>Temporadas nuevas</h3><p class="mu">Compara las temporadas que tienes con las que lista TMDB (solo series vinculadas con TMDB y con temporadas anotadas en la ficha).</p>
<button id="nv" style="background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;cursor:pointer">Buscar series con temporadas nuevas</button><div id="nvs"></div>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fil=f=>`<div class="f"><a href="/#t${f.id}">${esc(f.titulo)}</a> <span class="mu">(${f.anio||'sin año'}) · ${f.tipo}${f.fuente?' · '+esc(f.fuente):''}</span></div>`;
const sec=(t,l,ay)=>`<h3>${t} <span class="mu">(${l.length})</span></h3>${ay?`<p class="mu">${ay}</p>`:''}${l.length?l.slice(0,300).map(fil).join('')+(l.length>300?`<p class="mu">… y ${l.length-300} más</p>`:''):'<p class="mu">✓ Nada que revisar</p>'}`;
document.getElementById('nv').onclick=async()=>{const nv=document.getElementById('nvs');nv.textContent='Buscando…';
 try{const ids=await(await fetch('/api/series_tmdb')).json();let out=[];
  for(let i=0;i<ids.length;i+=10){const r=await(await fetch('/api/novedades',{method:'POST',body:JSON.stringify({ids:ids.slice(i,i+10)})})).json();if(r.error)throw new Error(r.error);out.push(...r);nv.textContent='Revisadas '+Math.min(i+10,ids.length)+' de '+ids.length+'…'}
  nv.innerHTML=out.length?out.map(s=>`<div class="f"><a href="/#t${s.id}">${esc(s.titulo)}</a> <span class="mu">tienes ${esc(s.tienes)} · TMDB: ${s.total} temporadas · ${s.estado=='Returning Series'?'en emisión':s.estado=='Ended'?'finalizada':esc(s.estado||'')}${s.proxima?' · próximo episodio '+esc(s.proxima):''}</span></div>`).join(''):`<p class="mu">✓ ${ids.length} series revisadas: ninguna tiene temporadas nuevas.</p>`}
 catch(x){nv.textContent=x.message}};
fetch('/api/calidad').then(r=>r.json()).then(r=>{document.getElementById('r').innerHTML=
 `${r.exactos?`<p style="color:#f5c26b">⚠ ${r.exactos} fichas repetidas exactas (misma ruta y mismo disco, por ejemplo por cerrar la página mientras se indexaba). <button id="de" style="background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:6px 10px;cursor:pointer">Eliminar copias sobrantes</button></p>`:''}<p class="mu">${r.desconectados} fichas apuntan a discos que ahora no están conectados (esas no se revisan).</p>`+
 `<h3>Posibles duplicados <span class="mu">(${r.duplicados.length})</span></h3><p class="mu">Mismo título y mismo año (o sin año). Las versiones nuevas con otro año no se marcan. Abre la ficha y usa Eliminar si sobra uno.</p>`+(r.duplicados.length?r.duplicados.map(g=>`<div class="g">${g.map(fil).join('')}</div>`).join(''):'<p class="mu">✓ Nada que revisar</p>')+
 sec('Sin carátula',r.sin_portada)+sec('Sin año',r.sin_anio)+sec('Sin sinopsis',r.sin_sinopsis)+sec('Archivo no encontrado',r.sin_archivo,'El disco está conectado, pero el archivo ya no está en esa ruta.');if(r.exactos)document.getElementById('de').onclick=async()=>{if(!confirm('¿Eliminar las copias sobrantes? Se conserva la primera de cada una.'))return;await fetch('/api/duplicados_exactos',{method:'POST',body:'{}'});location.reload()}});
</script></html>"""
PAGINA_VINC = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vincular con TMDB</title><style>body{margin:0;background:#141414;color:#f2f2f2;font:15px system-ui,sans-serif;padding:20px 4vw 60px}a{color:#6ab0ff}.mu{color:#9a9a9a}
input[type=text],button{background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;font-size:14px}button{cursor:pointer}.f{border-top:1px solid #2a2a2a;padding:12px 0}.f.rev{background:#2b2410}.f.no{background:#2b1414}
.op{display:flex;gap:10px;margin:6px 0;cursor:pointer}.op img{width:46px;height:69px;object-fit:cover;background:#333;border-radius:3px}#ap{background:#e50914;border-color:#e50914;margin:14px 0}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}@keyframes barra{0%{left:-35%}100%{left:100%}}</style>
<h1>Vincular fichas con TMDB</h1><p><a href="/">← Volver al catálogo</a></p>
<p class="mu">Busca en TMDB las fichas que aún no tienen enlace (las del catálogo antiguo) para completar su <b>tráiler</b>, su <b>colección</b> y las sinopsis vacías. No cambia lo que ya tengan. Las coincidencias con mismo título y año se marcan como seguras; el resto lo eliges tú.</p>
<p id="st" class="mu">Cargando…</p><button id="go" hidden>Buscar coincidencias</button> <label class="mu"><input type="checkbox" id="vs"> mostrar también las seguras</label>
<div id="res"></div><button id="ap" hidden>Aplicar</button><p id="fin"></p>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const $=id=>document.getElementById(id);let P=[],R=[],sel=[];
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(u,b){const r=await fetch(u,b?{method:'POST',body:JSON.stringify(b)}:{});const j=await r.json();if(j.error)throw new Error(j.error);return j}
api('/api/pendientes_tmdb').then(p=>{P=p;$('st').textContent=p.length?p.length+' fichas sin enlace de TMDB.':'Todas las fichas ya tienen enlace de TMDB.';$('go').hidden=!p.length});
$('go').onclick=async()=>{$('go').hidden=true;R=P.map(()=>null);sel=P.map(()=>-1);
 for(let i=0;i<P.length;i+=8){const r=await api('/api/proponer',{ids:P.slice(i,i+8).map(x=>x.id)});r.forEach((x,j)=>{R[i+j]=x;sel[i+j]=x.seguro?0:-1});$('st').textContent='Revisadas '+Math.min(i+8,P.length)+' de '+P.length+'…'}
 mostrar()};
$('vs').onchange=()=>mostrar();
function fila(i){const r=R[i],f=P[i];return `<div class="f ${r.cands.length?'rev':'no'}" id="f${i}"><b>${esc(f.titulo)}</b> <span class="mu">(${f.anio||'sin año'}) · ${f.tipo}</span><br>
 <input type="text" id="q${i}" value="${esc(f.titulo)}" size="34"> <button onclick="rebuscar(${i})">Buscar</button> <span class="mu">o pega una URL de TMDB/IMDb</span><div id="r${i}">${cands(i)}</div></div>`}
function cands(i){const r=R[i];return r.cands.length?r.cands.map((x,j)=>`<label class="op"><input type="radio" name="s${i}" ${sel[i]==j?'checked':''} onchange="sel[${i}]=${j}"><img src="${x.poster?'https://image.tmdb.org/t/p/w92'+x.poster:''}" alt=""><span><b>${esc(x.titulo)}</b> (${esc(x.anio)}) · ${x.media=='tv'?'serie':'película'}${x.original&&x.original!=x.titulo?' · '+esc(x.original):''}<br><small class="mu">${esc(x.resumen)}</small></span></label>`).join('')+`<label class="mu"><input type="radio" name="s${i}" ${sel[i]<0?'checked':''} onchange="sel[${i}]=-1"> Ninguna / omitir</label>`:`<span style="color:#ff8a8a">${esc(r.error||'Sin resultados.')} Prueba otro título o pega una URL.</span>`}
async function rebuscar(i){const q=$('q'+i).value.trim();try{const r=await api('/api/buscar?'+new URLSearchParams({q,tipo:P[i].tipo,anio:P[i].anio||''}));R[i].cands=r;R[i].error=null;sel[i]=-1}catch(x){R[i].cands=[];R[i].error=x.message}$('r'+i).innerHTML=cands(i)}
function mostrar(){const ver=$('vs').checked,idx=P.map((_,i)=>i).filter(i=>ver||!R[i].seguro),seg=R.filter(r=>r&&r.seguro).length;
 $('st').textContent=`${seg} coincidencias seguras (se aplicarán), ${R.filter(r=>r&&!r.seguro&&r.cands.length).length} para revisar, ${R.filter(r=>r&&!r.cands.length).length} sin resultados.`;
 $('res').innerHTML=idx.map(fila).join('');$('ap').hidden=false}
$('ap').onclick=async()=>{const items=P.map((f,i)=>({f,i})).filter(({i})=>sel[i]>=0&&R[i].cands[sel[i]]).map(({f,i})=>({id:f.id,media:R[i].cands[sel[i]].media,tmdb_id:R[i].cands[sel[i]].id}));
 if(!items.length)return;$('ap').hidden=true;let ok=0,err=[];
 for(let i=0;i<items.length;i+=15){$('fin').textContent='Aplicando '+Math.min(i+15,items.length)+' de '+items.length+'…';const r=await api('/api/vincular',{items:items.slice(i,i+15)});ok+=r.aplicados;err.push(...r.errores)}
 $('fin').innerHTML=`Listo: ${ok} fichas vinculadas${err.length?'; con error: '+esc(err.slice(0,5).join('; ')):''}. <a href="/">Ver catálogo</a>`};
</script></html>"""
PAGINA_AYUDA = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ayuda</title>
<style>body{margin:0;background:#141414;color:#f2f2f2;font:15px/1.55 system-ui,sans-serif;padding:20px 4vw 60px;max-width:860px}a{color:#6ab0ff}h2{margin:28px 0 6px;font-size:18px;color:#fff}.mu{color:#9a9a9a}code{background:#222;padding:1px 6px;border-radius:4px}li{margin:3px 0}</style>
<h1>Cómo se usa el catálogo</h1><p><a href="/">← Volver al catálogo</a></p>
<h2>Agregar películas y series</h2><ol><li>Menú <b>⚙ Herramientas → + Agregar disco o título</b>.</li><li>Escribe la carpeta (por ejemplo <code>E:\Peliculas</code>), el nombre del disco (<code>E02</code>) y si son películas o series. Pulsa <b>Escanear</b>.</li>
<li>El programa busca cada título en TMDB. Las filas en <b>amarillo</b> tienen dudas: elige la correcta, escribe otro título o pega una URL de TMDB o IMDb.</li><li>Pulsa <b>Guardar seleccionados</b>.</li></ol>
<p class="mu">Si agregas lo mismo dos veces no se duplica: se compara la ruta y el disco. Para un solo archivo, pega la ruta completa del archivo en vez de la carpeta. Para algo que el escáner no ve, usa <b>Agregar un título a mano</b> (al final de esa misma pantalla).</p>
<h2>Moverte por el catálogo</h2><ul><li>El título <b>MI CATÁLOGO</b> te lleva al inicio sin filtros. <b>☰ Ver todo</b> muestra todos los títulos en una grilla.</li><li>El buscador mira título, actores, director, país, año, idioma y más. Combina varias palabras.</li><li>Filtros: tipo, disco, colección, tu lista (por ver, vistos, favoritos, disponibles ahora), orden, géneros y letra inicial.</li><li><b>🎲 Sorpréndeme</b> elige al azar entre lo que estás viendo.</li></ul>
<h2>Marcar y puntuar</h2><p>Abre una ficha: <b>✓ Visto</b>, <b>⏱ Por ver</b>, <b>♥ Favorito</b> y 5 estrellas (un clic las pone, otro clic sobre la misma las quita). <b>✎ Editar</b> cambia datos, nota personal, carátula y tráiler; ahí también está <b>Eliminar título</b>.</p>
<h2>Seleccionar varios a la vez</h2><p><b>☑ Seleccionar</b> te deja marcar tarjetas y aplicar una acción a todas: colección, visto, por ver, favorito, cambiar tipo, agregar género o eliminar. <b>Todos los visibles</b> marca todo lo que coincide con tu búsqueda actual.</p>
<h2>Reproducir</h2><p>En la ficha, <b>▶ Reproducir</b> abre el video con tu reproductor. Si el disco no está conectado, te dice cuál conectar. El filtro <b>💾 Disponibles ahora</b> muestra solo lo que puedes ver en este momento.</p>
<h2>Mantener los datos limpios</h2><ul><li><b>Calidad de datos</b>: duplicados, fichas sin carátula, sin año, sin sinopsis o con archivo perdido; y elimina copias idénticas.</li><li><b>Vincular con TMDB</b>: para fichas antiguas, completa tráiler, colección y sinopsis.</li><li><b>Agregar disco → Mantenimiento</b>: corrige títulos en otros alfabetos, pone sinopsis en español, lee datos técnicos de los archivos (necesita ffmpeg) y actualiza las temporadas de las series desde sus carpetas.</li></ul>
<h2>Respaldos</h2><p>Al abrir el programa se guarda una copia de la base en <code>catalogo\respaldos</code> (las últimas 10), y otra antes de eliminar muchas fichas. Para tener una copia fuera de este computador, agrega en <code>config.json</code> la línea <code>"carpeta_respaldo": "C:\\Users\\TU-USUARIO\\OneDrive\\RespaldoCatalogo"</code>: el programa copia ahí cada respaldo y OneDrive, Google Drive o Dropbox la suben solos. <b>Exportar a CSV</b> descarga todo a una planilla, y <b>Restaurar desde CSV</b> la vuelve a cargar.</p>
<h2>Compartir el catálogo</h2><ul><li><b>Publicar versión web</b>: crea una página de solo lectura (sin rutas ni notas tuyas) que puedes subir a internet para que otra persona vea qué tienes y te haga pedidos por WhatsApp.</li><li>Dentro de tu casa: en <code>config.json</code> agrega <code>"acceso_red": true</code>; desde otros dispositivos de tu red se ve en modo solo lectura.</li></ul>
<p class="mu">Datos e imágenes: TMDB. Este producto usa la API de TMDB pero no está avalado ni certificado por TMDB.</p></html>"""
PAGINA_IMP = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Restaurar desde CSV</title>
<style>body{margin:0;background:#141414;color:#f2f2f2;font:15px system-ui,sans-serif;padding:20px 4vw 60px;max-width:760px}a{color:#6ab0ff}.mu{color:#9a9a9a}button{background:#e50914;color:#fff;border:0;border-radius:6px;padding:9px 14px;cursor:pointer;margin-top:12px}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}</style>
<h1>Restaurar desde CSV</h1><p><a href="/">← Volver al catálogo</a></p>
<p class="mu">Carga un CSV que hayas exportado con <b>Exportar a CSV</b>. Las fichas con el mismo número (id) se reemplazan y las que falten se agregan; no se borra nada más. Antes se guarda una copia de seguridad. Las carátulas no viajan en el CSV: se conservan las que ya estén en la carpeta <code>portadas</code>.</p>
<input type="file" id="f" accept=".csv,text/csv"><br><button id="go">Restaurar</button><p id="r"></p>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
document.getElementById('go').onclick=async()=>{const f=document.getElementById('f').files[0],r=document.getElementById('r');if(!f){r.textContent='Elige un archivo CSV.';return}
 try{const j=await(await fetch('/api/importar_csv',{method:'POST',body:JSON.stringify({texto:await f.text()})})).json();r.innerHTML=j.error?j.error.replace(/[<>&]/g,''):`Listo: ${j.importadas} fichas cargadas. <a href="/">Ver catálogo</a>`}catch(e){r.textContent=e.message}};
</script></html>"""
PAGINA_PUB = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Publicar versión web</title>
<style>body{margin:0;background:#141414;color:#f2f2f2;font:15px/1.55 system-ui,sans-serif;padding:20px 4vw 60px;max-width:820px}a{color:#6ab0ff}.mu{color:#9a9a9a}input{background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;font-size:14px}button{background:#e50914;color:#fff;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}code{background:#222;padding:1px 6px;border-radius:4px}h3{margin:26px 0 6px}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}</style>
<h1>Publicar versión web (solo lectura)</h1><p><a href="/">← Volver al catálogo</a></p>
<p>Crea una carpeta con una página que muestra tu catálogo: carátulas, sinopsis, géneros, tráileres y buscador. <b>No incluye</b> rutas de archivos, discos, notas, marcas de visto/favorito ni estrellas, y no permite editar nada. Quien la abra puede elegir títulos y enviarte el pedido por WhatsApp.</p>
<p>Número de WhatsApp para recibir los pedidos, con código de país y sin símbolos (ej: <code>56912345678</code>). Es opcional: si lo dejas vacío, quien pida elegirá a quién enviar el mensaje.</p>
<input id="wa" size="20" placeholder="56912345678"> <button id="go">Generar web</button><p id="r"></p>
<h3>Cómo ponerla en internet (gratis)</h3>
<ol><li>Entra a <b>app.netlify.com/drop</b> (puedes crear una cuenta gratis con tu correo).</li><li>Arrastra la carpeta <code>sitio</code> que generaste a esa página. Te entrega un enlace.</li><li>Envíale ese enlace a quien quieras. Cuando agregues títulos, vuelve a generar y a arrastrar la carpeta.</li></ol>
<p class="mu">Quien tenga el enlace puede ver tu lista, así que compártelo solo con quien corresponda. Las carátulas y datos son de TMDB, y la página incluye su aviso.</p>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const esc=s=>String(s).replace(/[<>&]/g,'');
document.getElementById('go').onclick=async()=>{const r=document.getElementById('r');try{const j=await(await fetch('/api/exportar_web',{method:'POST',body:JSON.stringify({whatsapp:document.getElementById('wa').value})})).json();
 if(j.error){r.textContent=j.error;return}r.innerHTML=`Listo: ${j.titulos} títulos (${j.mb} MB).<br>Carpeta: <code>${esc(j.carpeta)}</code> <button id="ab">Abrir carpeta</button>`;
 document.getElementById('ab').onclick=()=>fetch('/api/abrir_sitio',{method:'POST',body:'{}'})}catch(e){r.textContent=e.message}};
</script></html>"""
PAGINA_RESP = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Respaldos</title>
<style>body{margin:0;background:#141414;color:#f2f2f2;font:15px/1.55 system-ui,sans-serif;padding:20px 4vw 60px;max-width:820px}a{color:#6ab0ff}.mu{color:#9a9a9a}input{background:#222;color:#f2f2f2;border:1px solid #444;border-radius:6px;padding:8px 10px;font-size:14px;width:min(560px,92%)}
button{background:#333;color:#fff;border:0;border-radius:6px;padding:8px 13px;cursor:pointer;margin:3px 4px 3px 0}button.r{background:#e50914}code{background:#222;padding:1px 6px;border-radius:4px}h3{margin:26px 0 6px}#spin{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;pointer-events:none;background:#0007}#spin.on{display:flex}#spin>div{display:flex;flex-direction:column;align-items:center;gap:12px;background:#000e;color:#fff;padding:28px 44px;border-radius:18px;font:600 18px system-ui,sans-serif;box-shadow:0 10px 50px #000}#spin i{width:64px;height:64px;border:7px solid #fff3;border-top-color:#e50914;border-radius:50%;animation:giro .8s linear infinite}#spin small{font-weight:400;color:#bbb;font-size:14px}#spin b{position:fixed;top:0;left:0;height:5px;width:35%;background:#e50914;animation:barra 1.2s ease-in-out infinite}@keyframes giro{to{transform:rotate(360deg)}}</style>
<h1>Respaldos</h1><p><a href="/">← Volver al catálogo</a></p>
<p>Cada vez que abres el programa se guarda una copia de la base en <code>catalogo\\respaldos</code> (se conservan las últimas 10). Para tener otra copia <b>fuera de este computador</b>, elige una carpeta que sincronice <b>OneDrive, Google Drive o Dropbox</b>: el programa copia ahí cada respaldo y tu servicio de nube lo sube solo.</p>
<p id="cur" class="mu">Cargando…</p><div id="sug"></div>
<p><input id="ruta" placeholder="C:\\Users\\TU-USUARIO\\OneDrive\\RespaldoCatalogo"></p><button class="r" id="go">Guardar y hacer un respaldo ahora</button><button id="no">Quitar la carpeta adicional</button><p id="r"></p>
<h3>Cómo restaurar un respaldo</h3><ol><li>Cierra el programa.</li><li>Copia el archivo de respaldo que quieras (por ejemplo <code>catalogo_2026-11-04_103000.db</code>) a la carpeta <code>catalogo</code>.</li><li>Renómbralo a <code>catalogo.db</code>, reemplazando el actual, y abre el programa.</li></ol>
<p class="mu">El respaldo guarda los datos (fichas, marcas, notas, colecciones). Las carátulas están en la carpeta <code>catalogo\\portadas</code>; si quieres, cópiala también a tu nube de vez en cuando.</p>
<script>(()=>{let n=0,st=0,ht=0,t0=0,iv=0;const el=document.createElement('div');el.id='spin';el.innerHTML='<b></b><div><i></i><span>Trabajando…</span><small></small></div>';
const tic=()=>{el.querySelector('small').textContent=Math.round((Date.now()-t0)/1000)+' s'};
const mostrar=()=>{if(!el.classList.contains('on')){t0=Date.now();iv=setInterval(tic,1000);tic()}(document.querySelector('dialog[open]')||document.body).appendChild(el);el.classList.add('on')};
const ocultar=()=>{el.classList.remove('on');clearInterval(iv)};
const f=window.fetch;window.fetch=async(u,...a)=>{const lento=!/\/api\/(estado|marcar|disponibilidad)/.test(String(u));
 if(lento){n++;clearTimeout(ht);if(el.classList.contains('on'))mostrar();else if(!st)st=setTimeout(()=>{st=0;if(n)mostrar()},300)}
 try{return await f(u,...a)}finally{if(lento&&!--n){clearTimeout(st);st=0;ht=setTimeout(ocultar,400)}}}})();
const $=id=>document.getElementById(id),esc=s=>String(s).replace(/[<>&"]/g,'');
function mostrar(i){$('cur').innerHTML=i.extra?`Carpeta adicional actual: <code>${esc(i.extra)}</code>`:'Aún no hay carpeta adicional: los respaldos solo se guardan en este computador.';
 $('sug').innerHTML=i.sugerencias.length?'<p class="mu">Encontré estas carpetas en tu computador:</p>'+i.sugerencias.map(s=>`<button data-r="${esc(s.ruta)}">${esc(s.nombre)}: ${esc(s.ruta)}</button>`).join(''):'<p class="mu">No encontré OneDrive, Google Drive ni Dropbox. Escribe la ruta de cualquier carpeta (por ejemplo, en un pendrive).</p>'}
$('sug').onclick=e=>{if(e.target.dataset.r)$('ruta').value=e.target.dataset.r};
fetch('/api/respaldos').then(r=>r.json()).then(i=>{mostrar(i);if(i.extra)$('ruta').value=i.extra});
async function guardar(ruta){try{const j=await(await fetch('/api/respaldos_config',{method:'POST',body:JSON.stringify({ruta})})).json();if(j.error){$('r').textContent=j.error;return}
 $('r').innerHTML=j.extra?`Listo. Se guardó un respaldo ahora (<code>${esc(j.archivo)}</code>) en esa carpeta y se repetirá cada vez que abras el programa.`:'Carpeta adicional quitada.';mostrar(await(await fetch('/api/respaldos')).json())}catch(e){$('r').textContent=e.message}}
$('go').onclick=()=>guardar($('ruta').value);$('no').onclick=()=>guardar('');
</script></html>"""
MIME = {".html": "text/html; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, datos, mime, code=200):
        self.send_response(code); self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(datos))); self.end_headers(); self.wfile.write(datos)

    def _json(self, obj, code=200):
        self._enviar(json.dumps(obj, ensure_ascii=False).encode(), "application/json", code)

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if not self._local() and not (u.path in ("/", "/index.html", "/api/estado") or u.path.startswith(("/portadas/", "/fondos/"))):
                return self._enviar(b"Solo lectura desde este dispositivo", "text/plain", 403)
            if u.path == "/api/estado":
                return self._json({"local": self._local(), "e": estados()})
            if u.path == "/calidad":
                return self._enviar(PAGINA_CAL.encode(), "text/html; charset=utf-8")
            if u.path == "/respaldos":
                return self._enviar(PAGINA_RESP.encode(), "text/html; charset=utf-8")
            if u.path == "/api/respaldos":
                return self._json(info_respaldos())
            if u.path == "/api/portadas_pendientes":
                return self._json(portadas_pendientes())
            if u.path == "/api/fondos_pendientes":
                return self._json(fondos_pendientes())
            if u.path == "/ayuda":
                return self._enviar(PAGINA_AYUDA.encode(), "text/html; charset=utf-8")
            if u.path == "/importar":
                return self._enviar(PAGINA_IMP.encode(), "text/html; charset=utf-8")
            if u.path == "/publicar":
                return self._enviar(PAGINA_PUB.encode(), "text/html; charset=utf-8")
            if u.path == "/api/calidad":
                return self._json(revisar_calidad())
            if u.path == "/vincular":
                return self._enviar(PAGINA_VINC.encode(), "text/html; charset=utf-8")
            if u.path == "/api/pendientes_tmdb":
                return self._json(pendientes_tmdb())
            if u.path == "/api/exportar.csv":
                datos = exportar_csv().encode("utf-8-sig")
                self.send_response(200); self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="catalogo.csv"')
                self.send_header("Content-Length", str(len(datos))); self.end_headers(); self.wfile.write(datos)
                return
            if u.path == "/escanear":
                return self._enviar(PAGINA.encode(), "text/html; charset=utf-8")
            if u.path == "/colecciones":
                return self._enviar(PAGINA_COL.encode(), "text/html; charset=utf-8")
            if u.path == "/api/sugerir":
                return self._json(sugerir_colecciones())
            if u.path == "/api/disponibilidad":
                return self._json(disponibilidad())
            if u.path == "/api/series_tmdb":
                return self._json([r[0] for r in conectar().execute("SELECT id FROM titulos WHERE tipo='serie' AND url LIKE '%themoviedb.org/tv/%' AND temporadas IS NOT NULL")])
            if u.path == "/api/similares":
                return self._json(similares_tmdb(q["id"]))
            if u.path == "/api/buscar":
                return self._json(resolver(q["q"], q["tipo"], q.get("anio") or None))
            p = (CARPETA / (u.path.lstrip("/") or "index.html")).resolve()
            if CARPETA.resolve() in p.parents and p.is_file():
                return self._enviar(p.read_bytes(), MIME.get(p.suffix.lower(), "application/octet-stream"))
            self._enviar(b"No encontrado", "text/plain", 404)
        except Exception as e:
            self._json({"error": mensaje_error(e)}, 500)

    def do_POST(self):
        try:
            if not self._local():
                return self._json({"error": "Solo lectura desde otros dispositivos"}, 403)
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/escanear":
                return self._json(escanear(b["ruta"], b["tipo"], b.get("fuente")))
            if self.path == "/api/respaldos_config":
                return self._json(configurar_respaldo(b.get("ruta")))
            if self.path == "/api/portadas":
                return self._json(reparar_portadas(b["ids"]))
            if self.path == "/api/fondos":
                return self._json(descargar_fondos(b["ids"]))
            if self.path == "/api/importar_csv":
                return self._json(importar_csv(b["texto"]))
            if self.path == "/api/exportar_web":
                return self._json(exportar_web(b.get("whatsapp", "")))
            if self.path == "/api/abrir_sitio":
                abrir_carpeta(CARPETA / "sitio")
                return self._json({"ok": True})
            if self.path == "/api/temporadas_carpetas":
                return self._json(actualizar_temporadas())
            if self.path == "/api/lote":
                return self._json(lote(b["ids"], b["accion"], b.get("valor")))
            if self.path == "/api/duplicados_exactos":
                return self._json(eliminar_duplicados_exactos())
            if self.path == "/api/sinopsis_es":
                return self._json(sinopsis_a_espanol())
            if self.path == "/api/abrir":
                return self._json(abrir_en_pc(b["id"], b.get("modo", "archivo")))
            if self.path == "/api/novedades":
                return self._json(series_con_novedades(b["ids"]))
            if self.path == "/api/proponer":
                return self._json(proponer(b["ids"]))
            if self.path == "/api/vincular":
                return self._json(aplicar_vinculos(b["items"]))
            if self.path == "/api/tecnicos":
                return self._json(completar_tecnicos())
            if self.path == "/api/refrescar":
                return self._json(refrescar_todo() if not b.get("id") else {"corregidos": int(refrescar(b["id"]))})
            if self.path == "/api/marcar":
                marcar(b["id"], b.get("estado"), b.get("favorito"), b.get("estrellas"))
                return self._json({"ok": True})
            if self.path == "/api/borrar":
                borrar(b["id"])
                return self._json({"ok": True})
            if self.path == "/api/trailer":
                return self._json(buscar_trailer(b["id"]))
            if self.path == "/api/limpiar_colecciones":
                return self._json(limpiar_colecciones_solitarias())
            if self.path == "/api/colecciones":
                return self._json(aplicar_colecciones(b["grupos"]))
            if self.path == "/api/portada":
                cambiar_portada(b["id"], b.get("imagen"), b.get("url"))
                return self._json({"ok": True})
            if self.path == "/api/editar":
                editar(b["id"], b["campos"])
                return self._json({"ok": True})
            if self.path == "/api/guardar":
                ok, mal = [], []
                for it in b["items"]:
                    try:
                        n = guardar(it, b["fuente"])
                        if n: ok.append(n)
                    except Exception as e:
                        mal.append(f"{it.get('titulo')}: {e}")
                regenerar()
                return self._json({"guardados": ok, "errores": mal})
            self._enviar(b"No encontrado", "text/plain", 404)
        except Exception as e:
            self._json({"error": mensaje_error(e)}, 500)


def preparar_catalogo():
    """Si no hay catálogo todavía, crea uno vacío (para quien parte de cero, sin archivos .amc)."""
    if (CARPETA / "catalogo.db").exists():
        return
    from importar_amc import ESQUEMA
    (CARPETA / "portadas").mkdir(parents=True, exist_ok=True)
    db = conectar(); db.executescript(ESQUEMA); db.close()
    regenerar()
    print("Catálogo nuevo creado en", CARPETA)


def main():
    global CLAVE, RESPALDO_EXTRA
    cfg = AQUI / "config.json"
    CLAVE = os.environ.get("TMDB_API_KEY") or (json.loads(cfg.read_text()).get("tmdb_api_key", "") if cfg.exists() else "")
    RED = bool(cfg.exists() and json.loads(cfg.read_text()).get("acceso_red"))
    RESPALDO_EXTRA = json.loads(cfg.read_text()).get("carpeta_respaldo") if cfg.exists() else None
    if not CLAVE or CLAVE.startswith("PEGA"):
        cfg.write_text(json.dumps({"tmdb_api_key": "PEGA_AQUI_TU_CLAVE"}, indent=2))
        sys.exit(f"Falta la clave de TMDB. Pégala en {cfg} y vuelve a ejecutar.")
    preparar_catalogo()
    try:
        print("Respaldo creado:", respaldar())
    except Exception as e:
        print("No pude crear el respaldo:", e)
    normalizar_fuentes()
    regenerar()   # asegura que la página tenga la última versión
    srv = ThreadingHTTPServer(("0.0.0.0" if RED else "127.0.0.1", 8765), H)
    print("Catálogo en http://localhost:8765  (Ctrl+C para cerrar)")
    if RED:
        print(f"Desde celular o TV de tu red (solo lectura): http://{ip_local()}:8765")
    threading.Timer(1, lambda: webbrowser.open("http://localhost:8765")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
