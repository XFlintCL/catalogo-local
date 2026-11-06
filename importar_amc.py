#!/usr/bin/env python3
"""
Importa catálogos de Ant Movie Catalog (.amc, formato 4.2) a una base SQLite unificada.

Uso:
    python importar_amc.py --pelicula Peliculas.amc --serie series.amc --salida catalogo

Crea:
    catalogo/catalogo.db      base de datos (películas y series juntas, con índice de búsqueda)
    catalogo/portadas/*.jpg   una carátula por título (fuera de la base, para que sea rápida)
"""
import argparse, io, sqlite3, struct, datetime
from pathlib import Path

INTS = ["numero", "fecha", "i3", "color", "calificacion", "anio", "duracion",
        "bitrate_video", "bitrate_audio", "discos", "i4"]
STRS = ["campo_borrower", "medio", "tipo_medio", "fuente", "titulo_original", "titulo_traducido",
        "director", "productora", "guion", "musica", "pais", "generos", "certificacion",
        "reparto", "url", "sinopsis", "comentarios", "ruta_archivo", "formato_video",
        "formato_audio", "resolucion", "fps", "idiomas", "subtitulos", "tamano"]


def leer_amc(ruta):
    """Lee un .amc 4.2 y devuelve una lista de diccionarios (con la carátula en bytes)."""
    d = Path(ruta).read_bytes()
    pos = 0x55

    def entero():
        nonlocal pos
        v = struct.unpack_from("<i", d, pos)[0]; pos += 4
        return v

    def texto():
        nonlocal pos
        n = struct.unpack_from("<I", d, pos)[0]; pos += 4
        if n > 5_000_000:
            raise ValueError(f"Archivo no reconocido o dañado (longitud {n} en {pos-4:#x})")
        s = d[pos:pos + n]; pos += n
        return s.decode("cp1252", "replace")

    if b"AMC_4.2" not in d[:80]:
        raise ValueError(f"{ruta}: no parece un .amc versión 4.2")
    texto()  # propiedades de la imagen (ej. fw533:fh449)
    registros = []
    while pos < len(d) - 8:
        entero()  # separador
        r = {k: entero() for k in INTS}
        r["marcado"] = d[pos]; pos += 1
        for k in STRS:
            r[k] = texto()
        r["portada_ext"] = texto()
        n = entero()
        r["portada"] = d[pos:pos + n]; pos += n
        registros.append(r)
    return registros


def vacio(s):
    s = (s or "").replace("\r\n", "\n").strip()
    return s or None


def guardar_portada(datos, destino, ancho=200, alto=300):
    """Reduce la carátula a ~200x300 (JPEG) para que pese poco. Sin Pillow la guarda tal cual."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(datos)).convert("RGB")
        im.thumbnail((ancho, alto), Image.LANCZOS)
        im.save(destino, "JPEG", quality=82, optimize=True)
    except ImportError:
        destino.write_bytes(datos)


def fecha_delphi(n):
    if n <= 0:
        return None
    return (datetime.date(1899, 12, 30) + datetime.timedelta(days=n)).isoformat()


ESQUEMA = """
CREATE TABLE titulos (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('pelicula','serie')),
    numero_amc INTEGER,
    origen TEXT,
    titulo_original TEXT, titulo_traducido TEXT,
    anio INTEGER, duracion_min INTEGER, calificacion REAL,
    director TEXT, productora TEXT, guion TEXT, musica TEXT,
    pais TEXT, generos TEXT, certificacion TEXT, reparto TEXT,
    url TEXT, sinopsis TEXT, comentarios TEXT,
    medio TEXT, tipo_medio TEXT, ruta_archivo TEXT,
    formato_video TEXT, formato_audio TEXT, resolucion TEXT, fps TEXT,
    idiomas TEXT, subtitulos TEXT, tamano_mb INTEGER,
    bitrate_video INTEGER, bitrate_audio INTEGER, discos INTEGER,
    temporadas TEXT,       -- series: lo que había en el campo 'Prestado a' (S01, S01-S02, Completa...)
    prestado_a TEXT,       -- películas: préstamo real si lo hubiera
    fecha_agregado TEXT, portada TEXT, marcado INTEGER, color INTEGER,
    fuente TEXT,           -- disco duro de origen (E01, E02...)
    trailer TEXT,          -- clave de video de YouTube
    coleccion TEXT,        -- saga o colección (ej: Rambo)
    estado TEXT,           -- visto / pendiente
    favorito INTEGER,
    nota TEXT,             -- nota personal
    estrellas INTEGER,     -- mi puntuación (1 a 5)
    fondo TEXT             -- imagen ancha para los destacados
);
CREATE INDEX idx_titulos_fuente ON titulos(fuente);
CREATE INDEX idx_titulos_tipo ON titulos(tipo);
CREATE INDEX idx_titulos_anio ON titulos(anio);
CREATE INDEX idx_titulos_orig ON titulos(titulo_original COLLATE NOCASE);
CREATE VIRTUAL TABLE titulos_fts USING fts5(
    titulo_original, titulo_traducido, director, reparto, generos, sinopsis,
    content='titulos', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
"""


def entero_o_none(s):
    try:
        return int(str(s).strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pelicula", action="append", default=[], help="archivo .amc de películas (se puede repetir)")
    ap.add_argument("--serie", action="append", default=[], help="archivo .amc de series (se puede repetir)")
    ap.add_argument("--salida", default="catalogo", help="carpeta de salida")
    a = ap.parse_args()
    if not (a.pelicula or a.serie):
        ap.error("indica al menos un archivo con --pelicula o --serie")

    out = Path(a.salida); (out / "portadas").mkdir(parents=True, exist_ok=True)
    dbp = out / "catalogo.db"
    if dbp.exists():
        dbp.unlink()
    db = sqlite3.connect(dbp)
    db.executescript(ESQUEMA)

    total = {}
    for tipo, archivos in (("pelicula", a.pelicula), ("serie", a.serie)):
        for arch in archivos:
            regs = leer_amc(arch)
            for r in regs:
                cur = db.execute("INSERT INTO titulos(tipo, numero_amc, origen) VALUES (?,?,?)",
                                 (tipo, r["numero"], Path(arch).name))
                tid = cur.lastrowid
                portada = None
                if r["portada"]:
                    portada = f"portadas/{tid}.jpg"
                    guardar_portada(r["portada"], out / portada)
                borrower = vacio(r["campo_borrower"])
                db.execute("""UPDATE titulos SET
                    titulo_original=?, titulo_traducido=?, anio=?, duracion_min=?, calificacion=?,
                    director=?, productora=?, guion=?, musica=?, pais=?, generos=?, certificacion=?,
                    reparto=?, url=?, sinopsis=?, comentarios=?, medio=?, tipo_medio=?, ruta_archivo=?,
                    formato_video=?, formato_audio=?, resolucion=?, fps=?, idiomas=?, subtitulos=?,
                    tamano_mb=?, bitrate_video=?, bitrate_audio=?, discos=?, temporadas=?, prestado_a=?,
                    fecha_agregado=?, portada=?, marcado=?, color=?, fuente=? WHERE id=?""",
                    (vacio(r["titulo_original"]), vacio(r["titulo_traducido"]),
                     r["anio"] if r["anio"] > 0 else None,
                     r["duracion"] if r["duracion"] > 0 else None,
                     r["calificacion"] / 10 if r["calificacion"] > 0 else None,
                     vacio(r["director"]), vacio(r["productora"]), vacio(r["guion"]), vacio(r["musica"]),
                     vacio(r["pais"]), vacio(r["generos"]), vacio(r["certificacion"]),
                     (vacio(r["reparto"]) or "").replace(",\n", ", ") or None,
                     vacio(r["url"]), vacio(r["sinopsis"]), vacio(r["comentarios"]),
                     vacio(r["medio"]), vacio(r["tipo_medio"]), vacio(r["ruta_archivo"]),
                     vacio(r["formato_video"]), vacio(r["formato_audio"]), vacio(r["resolucion"]),
                     vacio(r["fps"]), vacio(r["idiomas"]), vacio(r["subtitulos"]),
                     entero_o_none(r["tamano"]),
                     r["bitrate_video"] if r["bitrate_video"] > 0 else None,
                     r["bitrate_audio"] if r["bitrate_audio"] > 0 else None,
                     r["discos"] if r["discos"] > 0 else None,
                     borrower if tipo == "serie" else None,
                     borrower if tipo == "pelicula" else None,
                     fecha_delphi(r["fecha"]), portada, r["marcado"], r["color"], vacio(r["tipo_medio"]), tid))
            total[tipo] = total.get(tipo, 0) + len(regs)
            print(f"{Path(arch).name}: {len(regs)} registros ({tipo})")

    db.execute("INSERT INTO titulos_fts(rowid, titulo_original, titulo_traducido, director, reparto, generos, sinopsis) "
               "SELECT id, titulo_original, titulo_traducido, director, reparto, generos, sinopsis FROM titulos")
    db.commit(); db.execute("VACUUM"); db.close()
    print(f"Listo: {sum(total.values())} títulos -> {dbp}")


if __name__ == "__main__":
    main()
