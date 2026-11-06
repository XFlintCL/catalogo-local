# Catálogo local de películas y series

Reemplazo moderno de *Ant Movie Catalog* para colecciones grandes repartidas en varios discos duros.
Todo corre en tu computador: una base SQLite, una interfaz web estilo Netflix y un escáner de carpetas
que completa los datos desde [TMDB](https://www.themoviedb.org/).

## Qué hace
- Vista de catálogo con carátulas, buscador, filtros por tipo (película/serie), género y **disco (fuente)**.
- Ficha con sinopsis, reparto, comentarios y ruta del archivo (hover o clic).
- **Escanea una carpeta** de un disco, reconoce los títulos por el nombre del archivo/carpeta y los busca en TMDB.
  Si hay dudas, eliges la coincidencia o pegas una URL de TMDB o de IMDb.
- Ficha con tráiler (YouTube), colecciones o sagas y filtro por letra inicial.
- Importa catálogos antiguos de Ant Movie Catalog (`.amc` v4.2).
- Carátulas reducidas (~200x300) para que todo pese poco.

## Requisitos
- Python 3.9 o superior y `pip install -r requirements.txt` (solo Pillow).
- Una clave gratuita de TMDB (API Key v3): themoviedb.org → Configuración → API.

## Uso
1. `python catalogo_app.py` (en Windows: doble clic en `Abrir catalogo.bat`).
2. La primera vez crea `config.json`: pega tu clave de TMDB y vuelve a ejecutar.
3. Se abre http://localhost:8765. Botón **+ Agregar disco** para escanear una carpeta.

Para migrar un catálogo de Ant Movie Catalog:
```
python importar_amc.py --pelicula Peliculas.amc --serie Series.amc --salida catalogo
python generar_web.py catalogo
```

## Más funciones
- Selección múltiple (colecciones, visto, favoritos, eliminar), estrellas, notas, "Disponibles ahora" y reproducir con un clic.
- Respaldos automáticos (opcionalmente a una carpeta sincronizada con OneDrive/Drive/Dropbox con `"carpeta_respaldo"` en `config.json`), exportar y restaurar desde CSV.
- **Versión web de solo lectura** para compartir el catálogo (sin rutas ni datos personales), con pedidos por WhatsApp.
- Revisión de calidad de datos y actualización de temporadas desde las carpetas.

## Estructura
| Archivo | Función |
|---|---|
| `catalogo_app.py` | Servidor local, escaneo de carpetas, búsqueda en TMDB y guardado |
| `importar_amc.py` | Lector del formato binario `.amc` 4.2 → SQLite |
| `generar_web.py` | Genera `catalogo/index.html` (la vista tipo Netflix) |

Tus datos viven en `catalogo/` (base de datos, carátulas y página). **Está en `.gitignore`: no lo subas.**

## Limitaciones conocidas / ideas para colaborar
- FilmAffinity no se puede consultar: no tiene API y bloquea a los programas (usa TMDB o IMDb).
- No lee formato, resolución ni idiomas de los archivos de video (podría usarse `ffprobe`).
- Los títulos con el año al inicio o en medio del nombre del archivo a veces se interpretan mal.
- No hay pruebas automáticas; tampoco soporte para otros idiomas de interfaz.

## Créditos y licencia
Código bajo licencia MIT (ver `LICENSE`).
Este producto usa la API de TMDB pero no está avalado ni certificado por TMDB. El uso de la API
es gratuito para proyectos no comerciales y cada persona debe usar su propia clave.
Ant Movie Catalog es obra de Antoine Potten; este proyecto no está afiliado a él.
