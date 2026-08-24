# Mapache Studio — Fase 9

Base de una plataforma para un estudio de fotografía profesional. Incluye arquitectura
Django, usuario personalizado, autenticación, dashboard, galerías, carga múltiple de
fotografías, procesamiento asíncrono, portafolio público, privacidad con PIN, auditoría y
pruebas. La Fase 4 suma un espacio de trabajo visual para organizar, revisar, publicar y
compartir galerías sin alterar el pipeline de medios. La Fase 5 incorpora Mapache AI:
búsqueda facial, búsqueda deportiva y búsqueda combinada estrictamente aisladas por
evento. La Fase 8 incorpora object storage configurable y entrega privada de media. La
Fase 9 añade originales descargables y paquetes ZIP privados generados en background.

## Versiones y requisitos

- Python 3.12 (compatible: 3.10–3.13)
- Django 5.2 LTS
- PostgreSQL 17 (PostgreSQL es el único motor soportado oficialmente)
- Redis 7 o posterior
- pgvector 0.8 o posterior
- OpenCV headless 4.10–4.x
- django-storages 1.14.6 (BSD-3-Clause)
- boto3/botocore 1.43.78 instalados actualmente (Apache-2.0)

Las dependencias se mantienen en `requirements/base.txt`, `development.txt` y
`production.txt`. Los rangos permiten recibir correcciones compatibles dentro de cada
línea estable.

## Instalación local

Crear y activar el entorno virtual:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/development.txt
```

Crear la configuración local:

```bash
cp .env.example .env
```

Completar todas las variables obligatorias en `.env`. `DJANGO_SECRET_KEY` debe ser una
clave larga y aleatoria; no debe versionarse.

## PostgreSQL

Crear el usuario y las bases con un rol PostgreSQL con permisos administrativos:

```sql
CREATE USER mapache_studio WITH PASSWORD 'una-contraseña-segura';
CREATE DATABASE mapache_studio OWNER mapache_studio;
CREATE DATABASE test_mapache_studio OWNER mapache_studio;
```

Hacer coincidir estos valores con `.env`. No existe fallback a SQLite: si falta una
variable, Django muestra un error explícito.

### pgvector

La extensión `vector` debe estar disponible en el mismo PostgreSQL que usa Django. En
macOS con PostgreSQL instalado mediante Homebrew:

```bash
brew install pgvector
```

En otros sistemas debe instalarse el paquete pgvector compatible con la versión exacta de
PostgreSQL. La migración inicial de `mapache_ai` ejecuta `CREATE EXTENSION vector`; el rol
que aplica migraciones debe ser propietario de la base o tener permiso para crearla.

Verificación:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

## Iniciar el proyecto

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Homepage: <http://127.0.0.1:8003/>
- Panel: <http://127.0.0.1:8003/dashboard/>
- Login: <http://127.0.0.1:8003/dashboard/login/>
- Galerías del panel: <http://127.0.0.1:8003/dashboard/galleries/>
- Portafolio público: <http://127.0.0.1:8003/portfolio/>
- Admin técnico: <http://127.0.0.1:8003/admin/>

## Galerías y privacidad

La app `apps.galleries` concentra los modelos `Gallery` y `Photo`, los selectores de
consulta y los servicios de negocio. Las galerías usan slugs únicos y estables: cambiar el
título no altera una URL ya compartida.

- `PUBLIC`: acceso directo y elegible para el portafolio cuando está publicada y tiene
  `show_in_portfolio=True`.
- `UNLISTED`: acceso directo mediante URL, pero nunca aparece en el portafolio.
- `PRIVATE_PIN`: requiere un PIN numérico de 4 a 8 dígitos.

Los PIN se procesan con los hashers de Django; el texto original nunca se guarda en la
base, la sesión, los templates ni la auditoría. Al abandonar la visibilidad privada se
elimina el hash. Cinco intentos fallidos bloquean nuevos intentos durante cinco minutos en
la sesión actual.

Rutas principales:

- `/dashboard/galleries/new/`: crear galería.
- `/dashboard/galleries/<uuid>/`: detalle y acciones de publicación.
- `/dashboard/galleries/<uuid>/edit/`: edición general.
- `/dashboard/galleries/<uuid>/access/`: privacidad, PIN y permisos de descarga.
- `/dashboard/galleries/<uuid>/photos/`: carga, estados y administración de fotografías.
- `/dashboard/galleries/<uuid>/share/`: metadatos seguros del enlace para copiar.
- `/dashboard/galleries/<uuid>/qr/`: QR PNG generado bajo demanda.
- `/dashboard/galleries/<uuid>/ai/`: activación, progreso y reindexación de Mapache AI.
- `/g/<slug>/`: galería publicada.
- `/g/<slug>/access/`: acceso mediante PIN.
- `/g/<slug>/find-me/`: búsqueda facial voluntaria cuando está habilitada.

## Procesamiento de fotografías

`apps.media_processing` valida el tamaño, la extensión y el contenido real de cada
archivo antes de guardar el original. Se admiten JPEG, PNG y WebP estáticos; las imágenes
animadas se rechazan explícitamente. El original permanece intacto.

Cada fotografía se procesa en la cola `media`:

1. normalización de EXIF Orientation;
2. detección de dimensiones y orientación;
3. versión WebP optimizada con lado máximo configurable (2400 px por defecto);
4. thumbnail WebP sin crop (600 px por defecto);
5. actualización del estado `PENDING → PROCESSING → READY` o `ERROR`.

El navegador divide cargas grandes en lotes configurables (8 archivos por defecto) y
muestra el avance real de transferencia. El servidor conserva el límite defensivo de 500
archivos por solicitud y 50 MB por fotografía; cada archivo se acepta o rechaza de forma
independiente.

Los derivados no conservan EXIF, incluyendo datos GPS. Los nombres internos usan UUID y
todo acceso a archivos pasa por la API de Django Storage, preparado para sustituir el
filesystem local en una fase posterior. La galería pública solo muestra derivados
optimizados de fotografías `READY`.

## Espacio de trabajo de galerías

El panel muestra trabajo reciente y una grilla fotográfica. La administración pagina 60
fotos, usa thumbnails en la grilla y reserva la versión optimizada para la previsualización;
el original nunca se renderiza allí. La selección masiva actúa solamente sobre la página
visible y pide confirmación en un modal propio.

El orden se puede cambiar con arrastre o controles `Anterior`/`Siguiente`. Al guardar se
envía el conjunto completo de UUIDs, por lo que el servidor detecta listas incompletas,
duplicadas o desactualizadas. Solo una foto `READY` puede ser portada. Eliminar la portada
limpia automáticamente la relación.

La publicación presenta una revisión de privacidad, portada, descargas y procesamiento.
Las fotos pendientes no bloquean la publicación: aparecen públicamente cuando llegan a
`READY`. Después de publicar se abre el modal para copiar el enlace o descargar el QR. El
QR contiene únicamente la URL pública estable; nunca incluye el PIN.

Configurar en cada entorno:

```env
PUBLIC_SITE_URL=https://galerias.example.com
MAPACHE_UPLOAD_BATCH_SIZE=8
```

`PUBLIC_SITE_URL` debe ser una URL HTTP(S) absoluta y es obligatoria en producción. La
generación del QR ocurre bajo demanda; no crea registros ni archivos persistentes.

## Mapache AI

`apps.mapache_ai` mantiene configuración, sesiones, indexación, búsqueda y adaptadores de
motor facial. `FaceEngine` separa el dominio del proveedor concreto; vistas, modelos y
tasks no llaman OpenCV directamente.

El adaptador incluido usa:

- detector OpenCV YuNet `face_detection_yunet_2023mar.onnx`;
- descriptor OpenCV SFace `face_recognition_sface_2021dec.onnx`;
- embedding de 128 dimensiones;
- similitud coseno;
- umbral inicial de similitud `0.363`, publicado para SFace/LFW por OpenCV.

Los modelos se ubican en `models/mapache_ai/`. Sus fuentes y checksums están en el README
de esa carpeta. OpenCV y los directorios de modelos declaran licencias permisivas, pero la
procedencia de entrenamiento del peso SFace exacto no está completamente documentada. Se
requiere una revisión de licencia/datasets antes de una explotación comercial. Esta
salvedad no debe interpretarse como asesoría legal.

El umbral `0.363` es un punto inicial del modelo, no una promesa de precisión. Debe
calibrarse con datos representativos y consentimiento apropiado antes de producción. La UI
no muestra scores ni porcentajes biométricos.

### Indexación

Solo se abren `optimized_file` de fotos `READY` mediante Django Storage. Cada rostro crea
un `FaceEmbedding`; `PhotoFaceIndex` registra también fotos procesadas con cero rostros. La
reindexación genera primero el resultado nuevo y reemplaza los embeddings de esa foto en
una transacción, por lo que un fallo conserva el índice anterior. Un índice HNSW con
`vector_cosine_ops` acelera búsqueda 1:N.

Al reprocesar una fotografía se eliminan sus embeddings obsoletos. Al eliminarla, las
relaciones `CASCADE` eliminan el índice automáticamente. Desactivar Mapache AI conserva el
índice para una reactivación rápida; borrarlo requiere la acción administrativa explícita
“Borrar índice”.

### Búsqueda y privacidad

La consulta vectorial siempre filtra simultáneamente `FaceEmbedding.gallery_id`,
`Photo.gallery_id` y estado `READY`. Nunca existe una búsqueda global o cross-gallery.
Mapache AI compara similitud dentro del evento; no identifica personas ni vincula una
identidad real.

La foto de referencia se valida, se lee y se cierra dentro de la misma request. No crea un
`Photo`, no se guarda en `MEDIA_ROOT`, no entra al cache y no se audita. Django puede usar
un temporal del sistema para uploads grandes; `uploaded.close()` lo elimina al terminar el
procesamiento. El embedding de consulta vive únicamente en memoria.

`FaceSearchSession` conserva solo consentimiento, galería, estado, cantidad y expiración.
Los Photo IDs ordenados viven en Redis con el mismo TTL (una hora por defecto), sin scores.
El comando siguiente elimina sesiones expiradas:

```bash
python manage.py cleanup_face_search_sessions
```

Las galerías `PRIVATE_PIN` continúan exigiendo la sesión de acceso existente. La búsqueda
tiene rate limit por sesión/IP contextual, configurable y respaldado por Redis en
producción.

### Variables

```env
MAPACHE_AI_ENABLED=False
MAPACHE_FACE_ENGINE=opencv_sface
MAPACHE_FACE_DETECTOR_MODEL=models/mapache_ai/face_detection_yunet_2023mar.onnx
MAPACHE_FACE_RECOGNIZER_MODEL=models/mapache_ai/face_recognition_sface_2021dec.onnx
MAPACHE_FACE_MATCH_THRESHOLD=0.363
MAPACHE_FACE_SEARCH_LIMIT=100
MAPACHE_FACE_QUERY_MAX_MB=10
MAPACHE_FACE_SEARCH_SESSION_TTL=3600
MAPACHE_FACE_CONSENT_VERSION=1.0
MAPACHE_FACE_SEARCH_RATE_LIMIT=10
MAPACHE_FACE_SEARCH_RATE_WINDOW=600
MAPACHE_BIB_ENGINE=tesseract
MAPACHE_BIB_MIN_CONFIDENCE=0.60
MAPACHE_BIB_SEARCH_LIMIT=100
MAPACHE_BIB_SEARCH_RATE_LIMIT=30
MAPACHE_BIB_SEARCH_RATE_WINDOW=600
MAPACHE_BIB_SEARCH_SESSION_TTL=3600
MAPACHE_BIB_OCR_TIMEOUT=20
MAPACHE_COMBINED_FACE_WEIGHT=0.5
MAPACHE_COMBINED_BIB_WEIGHT=0.5
MAPACHE_COMBINED_RRF_K=60
MAPACHE_COMBINED_SEARCH_LIMIT=100
MAPACHE_COMBINED_SEARCH_RATE_LIMIT=10
MAPACHE_COMBINED_SEARCH_RATE_WINDOW=600
MAPACHE_COMBINED_SEARCH_SESSION_TTL=3600
```

Producción requiere además `REDIS_URL`, los dos modelos instalados y activación explícita
de `MAPACHE_AI_ENABLED`. Los cambios de dimensión requieren un modelo y una migración
VectorField correspondientes; no basta cambiar una variable.

## Mapache AI Sports: búsqueda por dorsal

La búsqueda por número es un módulo independiente del motor facial. Cada galería activa
por separado `bib_search_enabled`, define formato `NUMERIC` o `ALPHANUMERIC` y configura
longitudes mínima/máxima. `GalleryAISettings` mantiene progreso facial y OCR separado.
Este buscador mantiene su señal independiente; la capa combinada descrita a continuación
consume sus resultados sin modificar el OCR.

El adaptador real usa Tesseract 5 con `pytesseract` y OpenCV. Tesseract detecta regiones de
texto disperso (`psm 11`) y entrega texto, confianza y caja; el adaptador prueba la imagen
redimensionada y una variante gris con contraste local. No se acepta cualquier número de
la foto: luego se aplican whitelist por formato, normalización, longitud, umbral y
deduplicación espacial. Todo se ejecuta localmente en CPU, sin servicio cloud obligatorio.

Tesseract y `pytesseract` usan Apache License 2.0. Referencias oficiales:

- https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE
- https://github.com/madmaze/pytesseract/blob/master/LICENSE
- https://pypi.org/project/pytesseract/

Instalación local en macOS:

```bash
brew install tesseract
pip install -r requirements/development.txt
tesseract --version
```

En Linux debe instalarse el paquete del sistema `tesseract-ocr` además de las dependencias
Python. La imagen optimizada se abre exclusivamente mediante Django Storage API. La tarea
`index_gallery_bibs` crea un trabajo `index_photo_bibs` por fotografía en la cola `ai`;
una foto con cero dorsales queda correctamente marcada como analizada. Una reejecución
reemplaza sus detecciones dentro de una transacción y un fallo conserva el índice anterior.

La normalización conserva strings y ceros iniciales. En `NUMERIC`, y solo en ese formato,
convierte confusiones OCR controladas (`O→0`, `I/L→1`, `S→5`, `B→8`). En
`ALPHANUMERIC` preserva letras, por lo que `B12` no se convierte en `812`. La búsqueda es
exacta, usa el índice `(gallery, normalized_number)`, filtra además la galería de `Photo` y
su estado `READY`, y devuelve cada fotografía una vez.

El umbral inicial `MAPACHE_BIB_MIN_CONFIDENCE=0.60` es un punto de partida operativo, no
una verdad universal: debe calibrarse con fotografías representativas de cada deporte,
distancia, movimiento y diseño de dorsal. El smoke sintético local detectó `247` con
confianza aproximada `0.90` en unos `250 ms` en este equipo; no implica throughput de
producción ni reemplaza pruebas con un dataset real.

Operación y smoke test:

```bash
python manage.py reindex_gallery_bibs <gallery_uuid>
python manage.py cleanup_bib_search_sessions
celery -A config worker -l INFO -Q ai
MAPACHE_RUN_BIB_INTEGRATION=1 pytest tests/test_mapache_ai_bib_integration.py
```

La consulta pública respeta publicación, acceso `PRIVATE_PIN`, rate limit y aislamiento por
galería. `BibSearchSession` guarda únicamente galería, número normalizado, conteo y
expiración; no vincula dorsales a nombres ni crea perfiles de atletas. El resultado vive
temporalmente en cache y nunca expone texto OCR, confianza o cajas.

Nota de compliance facial: revisar la procedencia y licencia de los pesos YuNet/SFace antes
del uso comercial definitivo. Esta revisión no bloquea el módulo OCR Apache 2.0.

## Mapache AI: búsqueda combinada rostro + dorsal

La búsqueda combinada es una capa de orquestación; no reemplaza ni duplica
`run_face_query`/`search_faces_in_gallery` ni `search_bibs_in_gallery`. Solo está disponible
cuando la galería tiene activas ambas funciones. Recibe la selfie y el dorsal, ejecuta los
dos buscadores existentes y rankea la **unión** de sus Photo IDs. Por ello conserva fotos
halladas únicamente por rostro o únicamente por número; no usa una intersección estricta.

Los scores de similitud facial y confianza OCR no son magnitudes directamente comparables.
La fusión usa Reciprocal Rank Fusion (RRF): para cada señal asigna `1 / (K + posición)` y
calcula la suma ponderada. Los pesos iniciales son 0.5/0.5 y `K=60`. Una fotografía presente
en ambas listas acumula las dos contribuciones y normalmente sube en el ranking. Los pesos,
`K` y el límite se configuran mediante las variables `MAPACHE_COMBINED_*`; deben calibrarse
con búsquedas representativas antes de alterar sus valores.

`CombinedSearchSession` registra galería, dorsal normalizado, consentimiento facial,
conteos agregados y expiración. Los resultados ordenados viven en cache únicamente como
Photo IDs: no se persisten selfies, embeddings de consulta, scores combinados ni una
asociación dorsal-persona. El rate limit combinado es independiente, pero conserva el
límite inicial del flujo biométrico. El comando de limpieza es:

```bash
python manage.py cleanup_combined_search_sessions
```

El endpoint y su sesión vuelven a comprobar publicación, `PRIVATE_PIN`, activación de ambas
señales y `gallery_id`. Los resultados no muestran scores ni explicaciones biométricas. No
se creó un índice combinado: cualquier reindexación continúa operándose por separado en
los módulos de rostro y dorsal.

## Redis y Celery en macOS

Instalar e iniciar Redis con Homebrew:

```bash
brew install redis
brew services start redis
redis-cli ping
```

Configurar `REDIS_URL`, `CELERY_BROKER_URL` y `CELERY_RESULT_BACKEND` en `.env`. Después,
en terminales separadas, iniciar Django y los workers:

```bash
python manage.py runserver
celery -A config worker -l INFO -Q media,ai
# O un worker AI independiente:
celery -A config worker -l INFO -Q ai
```

El flujo asíncrono normal necesita PostgreSQL, Redis, Django y un worker Celery activo.
La cola `ai` programa una tarea por fotografía; ninguna request HTTP procesa una galería
completa secuencialmente. `downloads` permanece reservada. Las pruebas usan broker/result
backend en memoria y un `FakeFaceEngine`, por lo que no cargan modelos pesados.

## Object storage

Media usa la configuración moderna `STORAGES` de Django. `default` se selecciona de forma
explícita con `STORAGE_BACKEND`; `staticfiles` continúa local y no se mueve a R2. Los
modelos conservan únicamente las keys de sus `FileField`, con esta estructura:

```text
galleries/<gallery_uuid>/originals/<photo_uuid>.<ext>
galleries/<gallery_uuid>/optimized/<photo_uuid>.webp
galleries/<gallery_uuid>/thumbnails/<photo_uuid>.webp
```

Todo el pipeline abre, guarda y elimina mediante Django Storage API. No requiere `.path`.
Las URLs se generan en `apps.core.media_delivery`: los templates nunca construyen rutas ni
leen directamente `.url`. El original se considera privado y solo puede solicitarlo una
operación administrativa explícita. La galería pública y los resultados facial, dorsal y
combinado usan exclusivamente thumbnails o versiones optimizadas.

### Desarrollo local

```env
STORAGE_BACKEND=local
MAPACHE_PRIVATE_MEDIA_URL_TTL=900
MAPACHE_PUBLIC_MEDIA_URL_TTL=3600
```

PUBLIC y UNLISTED usan `/media/` en desarrollo. Para PRIVATE_PIN, Django emite una ruta
firmada temporal y vuelve a validar tanto la sesión de acceso como su expiración antes de
abrir el derivado. Los originales nunca pasan por esa ruta.

### Cloudflare R2

Crear un bucket privado y un API token limitado al bucket con lectura y escritura de
objetos. No habilitar `r2.dev` ni hacer público el bucket único: hacerlo expondría también
originales y derivados PRIVATE_PIN a quien conociera la key. Configurar el entorno:

```env
STORAGE_BACKEND=r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=
R2_CUSTOM_DOMAIN=
MAPACHE_PRIVATE_MEDIA_URL_TTL=900
MAPACHE_PUBLIC_MEDIA_URL_TTL=3600
```

`R2_ENDPOINT_URL` puede quedar vacío para usar
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`. Las firmas usan SigV4, región `auto`
y direccionamiento por path. WebP se guarda como `image/webp`; los derivados se entregan
`inline` y con cache privada limitada, mientras los originales usan `private, no-store`.
PUBLIC y UNLISTED reciben URLs R2 firmadas de una hora; PRIVATE_PIN y dashboard reciben
URLs de quince minutos por defecto. La autorización ocurre antes de firmar.

`R2_CUSTOM_DOMAIN` queda disponible para una arquitectura CDN posterior, pero no se aplica
automáticamente al bucket privado. Cloudflare no permite usar presigned URLs sobre custom
domains. Activarlo de forma segura exige separar derivados públicos en otro bucket o
interponer un Worker/WAF que autorice y firme; apuntar directamente el dominio al bucket
actual rompería PRIVATE_PIN. No se implementa esa ampliación en esta fase.

Si en el futuro el navegador consume media mediante JavaScript o canvas desde otro origen,
la política CORS del bucket/dominio debe permitir solo los orígenes reales de Mapache
Studio, métodos `GET` y `HEAD`, y los headers estrictamente necesarios; no usar `*`. El
proyecto no define actualmente CSP. Si se agrega una, incluir el endpoint/dominio elegido
en `img-src` sin relajar las demás directivas.

### Verificación y migración

La prueba profunda crea un objeto único, comprueba escritura, lectura y eliminación y lo
limpia al terminar:

```bash
python manage.py check_storage
```

La migración controlada copia desde media local al storage configurado. Nunca borra el
origen, conserva las keys, permite probar una sola galería y valida existencia, tamaño y
SHA-256. Un destino idéntico se marca `SKIPPED`; un conflicto distinto falla de forma
aislada y no se sobrescribe.

```bash
python manage.py migrate_media_to_storage --dry-run
python manage.py migrate_media_to_storage --gallery <gallery_uuid>
# Origen local alternativo:
python manage.py migrate_media_to_storage --source-root /ruta/media
```

El reporte incluye `Copied`, `Skipped`, `Failed` y `Bytes transferred`. Antes de cambiar
tráfico, ejecutar primero dry-run, después una galería piloto, comparar el reporte y abrir
dashboard, galería pública, PRIVATE_PIN y Mapache AI. Con credenciales de un bucket de
prueba, el smoke opcional mide escritura/lectura/eliminación sin publicarlo como SLA:

```bash
MAPACHE_RUN_R2_INTEGRATION=1 pytest tests/test_storage_r2_integration.py -m integration
```

Las credenciales solo se leen del entorno y `.env` está ignorado.

### Upload directo R2, multipart y reanudación

El upload profesional se activa únicamente cuando `MAPACHE_DIRECT_UPLOAD_ENABLED=True` y
`STORAGE_BACKEND=r2`. En local permanece automáticamente el flujo tradicional
`browser → Django → Storage`. En R2, Django funciona como plano de control: valida la
galería y la metadata, genera una key definitiva con UUID y entrega una URL SigV4 temporal;
los bytes viajan `browser → R2` y nunca atraviesan Django.

```env
MAPACHE_DIRECT_UPLOAD_ENABLED=True
MAPACHE_DIRECT_UPLOAD_MAX_FILES=5000
MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES=1099511627776
MAPACHE_UPLOAD_URL_TTL=900
MAPACHE_MULTIPART_UPLOAD_THRESHOLD_MB=25
MAPACHE_MULTIPART_PART_SIZE_MB=10
MAPACHE_UPLOAD_CONCURRENCY=4
MAPACHE_UPLOAD_SESSION_TTL=86400
```

Los archivos de hasta 25 MB usan presigned PUT. Los mayores usan S3 Multipart compatible
con R2, partes de 10 MB y URLs solicitadas en bloques de hasta 100. El navegador limita la
concurrencia a cuatro transferencias, muestra progreso de bytes real mediante
`XMLHttpRequest`, reintenta cada archivo o parte hasta tres veces con backoff y permite
cancelarlos. Las URL firmadas usan siempre el endpoint S3 de R2, nunca un custom domain.

`GalleryUploadBatch` registra el conjunto y `GalleryUploadItem` cada objeto. No se crea
`Photo` antes de comprobar con HEAD que la key exacta existe y su tamaño coincide. La
confirmación es idempotente, apunta `original_file` a la misma key (sin copiarla) y programa
`process_photo` en la cola `media`; la validación real con Pillow, WebP, thumbnails y hooks
facial/dorsal siguen siendo los existentes.

El UUID del lote queda en almacenamiento local del navegador. Tras recargar, el dashboard
avisa de una carga sin terminar y consulta las partes existentes con `list_parts`. Por las
reglas del navegador, el usuario debe volver a seleccionar sus archivos; Mapache los
asocia por nombre, tamaño y `lastModified`, y vuelve a subir únicamente lo faltante. No se
promete acceso persistente al archivo local.

La limpieza idempotente usa los UploadItem como fuente de verdad; no lista el bucket
completo, aborta multipart, elimina PUT huérfanos y nunca elimina una Photo confirmada:

```bash
python manage.py cleanup_direct_uploads
```

También existe `apps.galleries.tasks.cleanup_expired_direct_uploads`, preparada para un
scheduler futuro y enrutada a `media`.

Configurar CORS del bucket con los orígenes exactos que sirvan el dashboard. Esta es la
política de producción recomendada; agregar otros orígenes concretos por ambiente, nunca
`*`:

```json
[
  {
    "AllowedOrigins": ["https://mapachestudio.com"],
    "AllowedMethods": ["PUT", "HEAD"],
    "AllowedHeaders": ["Content-Type"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

Los endpoints Django son privados, requieren sesión y CSRF, y no aceptan una key aportada
por el navegador. La respuesta nunca contiene access key, secret key ni upload ID de R2.
El token de Django debe limitarse al bucket específico y a lectura/escritura de objetos y
operaciones multipart; no usar un token administrativo global. Los logs `mapache.uploads`
pueden registrar UUID, galería, modo, tamaño, estado y duración, pero nunca URL firmada ni
credenciales.

## Descargas individuales y ZIP

Los campos existentes `Gallery.allow_photo_download` y
`Gallery.allow_gallery_download` son la única fuente de verdad. La interfaz oculta los
controles deshabilitados y cada endpoint vuelve a comprobar publicación, pertenencia de la
fotografía, flag y acceso PRIVATE_PIN. Una descarga individual entrega siempre
`original_file` con `Content-Disposition: attachment`; nunca publica `.url` ni utiliza el
derivado WebP.

En local, Django abre el original mediante Storage API y responde con `FileResponse`. En
R2, genera una URL SigV4 temporal con nombre y MIME de descarga. El TTL se configura en:

```env
MAPACHE_DOWNLOAD_URL_TTL=900
```

La descarga completa se solicita mediante POST y crea un `GalleryDownload` asociado al
hash SHA-256/HMAC de la sesión. El identificador de sesión nunca se persiste en claro. La
creación se programa con `transaction.on_commit` y `build_gallery_download` se ejecuta
exclusivamente en la cola `downloads`:

```bash
celery -A config worker -l INFO -Q downloads
```

El ZIP incluye solo fotografías `READY` con original, respeta `Photo.sort_order` y usa
nombres deterministas como `001_DSC_2412.jpg`. Los nombres eliminan rutas y caracteres de
control. Se usa `ZIP_STORED` porque JPEG/PNG ya están comprimidos, `allowZip64=True` y
`force_zip64=True` para paquetes superiores a 4 GB. Cada original se copia por bloques
desde Storage API hacia un ZIP temporal; nunca se carga la galería completa en RAM. El
temporal se elimina tanto en éxito como en excepción.

Antes de generar se comprueba existencia y tamaño de todos los originales, el límite de
fotografías y el espacio temporal disponible. Un original faltante produce `ERROR`; no se
guarda ni entrega un paquete parcial. Los límites son:

```env
MAPACHE_GALLERY_DOWNLOAD_MAX_PHOTOS=10000
# 0 desactiva el límite total de bytes:
MAPACHE_GALLERY_DOWNLOAD_MAX_BYTES=0
```

`content_fingerprint` incluye orden, UUID, key, nombre, tamaño y actualización de cada
fotografía elegible. Una descarga `READY` solo se reutiliza en la misma sesión cuando no ha
expirado, el archivo existe y la fingerprint coincide. Agregar, eliminar, reordenar o
cambiar un original fuerza un paquete nuevo. El constraint de base de datos y el bloqueo
de Gallery evitan dos generaciones idénticas simultáneas.

La tarea actualiza `processed_photos` cada diez fotografías y al terminar. La pantalla de
preparación consulta el estado cada cuatro segundos y se detiene en `READY`, `ERROR` o
`EXPIRED`; nunca recibe keys, hashes, URLs firmadas ni errores técnicos. Los ZIP vencen
después de 24 horas por defecto:

```env
MAPACHE_GALLERY_DOWNLOAD_TTL=86400
```

La limpieza puede ejecutarse manualmente y también existe una tarea Celery preparada para
una programación futura, sin instalar Beat:

```bash
python manage.py cleanup_gallery_downloads
```

El comando elimina el objeto, vacía su FileField y marca `EXPIRED`; tolera objetos ya
ausentes y es idempotente. Desde el dashboard, “Invalidar descargas preparadas” aplica la
misma revocación a paquetes pendientes, en proceso o listos y registra
`GALLERY_DOWNLOADS_INVALIDATED` en auditoría. DRAFT y ARCHIVED nunca pueden solicitar,
consultar ni descargar ZIP antiguos.

Los ZIP quedan bajo
`downloads/galleries/<gallery_uuid>/<download_uuid>.zip`. En R2 siguen privados y su
descarga final usa una URL firmada con `attachment`; localmente no se expone el directorio.
Los errores transitorios de lectura/escritura tienen hasta tres reintentos con backoff. No
se registran PIN, tokens, URLs firmadas ni credenciales.

## Calidad y pruebas

Las pruebas usan PostgreSQL y la base indicada por `POSTGRES_TEST_DB`.

```bash
pytest
ruff check .
ruff format --check .
python manage.py check
python manage.py makemigrations --check
```

Los smoke tests opcionales cargan los adaptadores reales configurados:

```bash
MAPACHE_RUN_FACE_INTEGRATION=1 pytest -m integration
MAPACHE_RUN_BIB_INTEGRATION=1 pytest tests/test_mapache_ai_bib_integration.py
```

La suite vectorial principal sí usa PostgreSQL/pgvector real, pero el Fake engine hace sus
resultados deterministas. No se publica una cifra de latencia de inferencia porque esta
fase no incluye un dataset facial consentido y representativo para medirla. Los logs
operacionales incluyen duración en milisegundos para indexación y búsqueda, sin imágenes,
embeddings ni resultados biométricos.

## Entornos

- `config.settings.development`: desarrollo local y logging en consola.
- `config.settings.test`: pruebas rápidas con PostgreSQL.
- `config.settings.production`: cookies seguras, HTTPS, HSTS y hosts obligatorios.

Producción debe iniciar con `DJANGO_SETTINGS_MODULE=config.settings.production` y exige
broker/result backend explícitos. Con `STORAGE_BACKEND=r2`, Django y todos los workers
Celery deben recibir las mismas variables R2. Los archivos locales en `media/` no forman
parte del despliegue de producción. El worker de descargas necesita acceso al mismo
storage y espacio temporal suficiente. El worker `media` necesita las mismas credenciales
R2 para leer originales directos y guardar los derivados.
