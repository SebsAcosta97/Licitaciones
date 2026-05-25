# TFM - Scraping de Contratacion del Estado

Proyecto base para extraer enlaces de licitaciones desde el portal oficial:

`https://contrataciondelestado.es/.../FormularioBusqueda.jsp`

## 1) Crear y activar entorno virtual

```powershell
cd "c:\Users\sebas\iCloudDrive\Documents\Sebastian\08_Python\00_EAE\TFM_scraping_contratacion_estado"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 2) Instalar dependencias

```powershell
pip install -r requirements.txt
```

## 3) Ejecutar scraping

Sin filtro:

```powershell
python .\scraper_contratacion.py
```

Con una consulta (ejemplo):

```powershell
python .\scraper_contratacion.py --query "mantenimiento"
```

## 4) Salidas generadas

Se crean en `data/`:

- `raw_page.html`: HTML bruto para auditoria/repetibilidad.
- `licitaciones.json`: enlaces extraidos en JSON.
- `licitaciones.csv`: enlaces extraidos en CSV.
- `licitaciones.txt`: enlaces extraidos en TXT.

## Notas importantes para el TFM

- Este arranque usa scraping HTTP basico (`requests + bs4`) para asegurar compatibilidad con Python 3.14.
- El portal tiene contenido dinamico; esta version es para iniciar analisis rapido con CSV/TXT.
- Si quieres, el siguiente paso es una version avanzada con navegador para paginacion y detalle de expedientes.
