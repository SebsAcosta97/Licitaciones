# Memoria Técnica Profesional
## Plataforma GovTech de Inteligencia para Contratación Pública

**Autor:** Sebastián Acosta (editar si aplica)  
**Programa:** Máster en Big Data & Analytics  
**Institución:** (Editar universidad/escuela)  
**Fecha:** 25 de mayo de 2026

---

## 1. Resumen Ejecutivo
Este proyecto desarrolla una plataforma GovTech orientada a la detección de oportunidades de contratación pública abiertas y recientes en España. La solución integra scraping de fuentes oficiales, transformación de datos a capas de consumo diferenciadas y un frontend estático optimizado para GitHub Pages.

El problema abordado es doble: por un lado, la fragmentación y complejidad de la información pública; por otro, la baja usabilidad de muchos portales institucionales para usuarios empresariales que necesitan priorizar oportunidades con rapidez. La propuesta resuelve este gap con una arquitectura de datos en dos niveles: histórico analítico y dataset operativo para interfaz.

El valor principal del sistema es permitir discovery comercial de licitaciones activas con filtros rápidos, KPIs operativos y una narrativa de insights asistida por IA, manteniendo simplicidad de despliegue (frontend-only) y bajo coste operativo.

## 2. Introducción
La contratación pública representa una fuente estratégica de negocio para empresas de múltiples sectores. Sin embargo, la extracción de valor desde datos abiertos públicos suele verse limitada por tres barreras: heterogeneidad semántica, volumen histórico elevado y experiencia de usuario poco orientada a toma de decisiones.

Desde la perspectiva GovTech, este TFM plantea una aproximación práctica: construir una capa de inteligencia de oportunidades que transforme datos públicos en señales accionables para preparación de ofertas. El diseño se alinea con principios de Big Data & Analytics: ingestión, limpieza, modelado para consumo, observabilidad de pipeline y optimización de rendimiento de capa de presentación.

## 3. Objetivos
### 3.1 Objetivo general
Diseñar e implementar una plataforma técnica de detección y exploración de licitaciones públicas abiertas, con una arquitectura de datos optimizada para analítica e interfaz de usuario en entorno estático.

### 3.2 Objetivos específicos
- Automatizar la extracción de licitaciones desde fuentes oficiales.
- Normalizar y consolidar registros en un dataset histórico.
- Crear una capa de serving frontend ligera orientada a oportunidades activas.
- Implementar un dashboard web con filtros, búsqueda y KPIs operativos.
- Resolver restricciones de despliegue GitHub Pages + Git LFS.
- Documentar incidencias técnicas, causas raíz y correcciones aplicadas.

## 4. Arquitectura General del Sistema
La arquitectura implementada sigue un patrón ETL + Serving Layer + Frontend estático.

```text
[Fuentes oficiales ATOM / Web]
          |
          v
[scraper_contratacion.py]
          |
          v
[data/licitaciones.json]  (histórico completo, LFS)
          |
          v
[generate_dashboard_json.py]
          |
          v
[data/licitaciones_abiertas.json]  (dataset frontend)
          |
          v
[index.html + JS Vanilla + Tailwind]
          |
          v
[GitHub Pages]
```

### Decisiones clave
- Separación estricta entre datos históricos y datos de consumo UI.
- Eliminación de dependencias runtime del frontend a datasets pesados.
- Compatibilidad total con hosting estático (sin backend).

## 5. Arquitectura de Datos
### 5.1 Datasets de operación frontend
- `data/licitaciones_abiertas.json`.
- Contenido: registros abiertos/activos/recientes y campos mínimos de interfaz.
- Uso: cards, filtros, búsqueda, KPIs operativos, insights.

### 5.2 Datasets históricos/analíticos
- `data/licitaciones.json` (histórico licitaciones).
- `data/documentos_licitacion.json` (histórico documental).
- Uso: análisis profundo, investigaciones, futuras capacidades IA.

### 5.3 Política Git LFS
Solo se almacenan en LFS:
- `data/licitaciones.json`
- `data/documentos_licitacion.json`

Esto evita servir punteros LFS al frontend operativo y reduce errores de parseo en navegador.

## 6. Proceso de Scraping
El módulo `scraper_contratacion.py` realiza la ingesta de datos públicos siguiendo este flujo:
1. Consulta de feeds oficiales de contratación.
2. Parseo de entradas y extracción de atributos de licitación.
3. Normalización de campos clave (estado, importes, fechas, órgano, localización).
4. Persistencia incremental en dataset histórico.
5. Exportaciones auxiliares para trazabilidad y auditoría.

El diseño prioriza resiliencia ante campos faltantes y variabilidad semántica entre publicaciones.

## 7. Procesamiento y Optimización
El script `generate_dashboard_json.py` implementa una capa de serving orientada a frontend:
- Filtrado por estado abierto/activo.
- Filtrado temporal por recencia.
- Limpieza de registros corruptos o no útiles.
- Normalización de importes y fechas.
- Dedupe por identificador de licitación.
- Reducción de columnas a esquema mínimo de consumo.

### Esquema operativo frontend
| Campo | Propósito |
|---|---|
| licitacion_id | Identificador único |
| title | Título de la oportunidad |
| organo_contratacion | Entidad convocante |
| estado_codigo | Estado de expediente |
| tipo_contrato_codigo | Tipología contractual |
| importe_total | Magnitud económica |
| fecha_publicacion | Recencia |
| lugar_ejecucion | Segmentación territorial |
| url | Acceso a detalle oficial |

## 8. Frontend y Experiencia de Usuario
El frontend está implementado en un único `index.html` con Tailwind + JavaScript Vanilla, optimizado para GitHub Pages.

### Principios UX aplicados
- Carga inicial con feed activo (sin exigir búsqueda previa).
- Filtros visibles y compactos para refinado rápido.
- Resultados escaneables con cards de baja densidad cognitiva.
- KPIs operativos para contexto inmediato de mercado.
- Mensajes de loading/empty/error orientados a claridad.

### Componentes funcionales
- Búsqueda semántica simple por texto libre.
- Filtros por estado, tipo, ubicación, fecha y presupuesto.
- Badges de oportunidad (abierto, urgente, alto presupuesto, etc.).
- Render incremental (lazy/paginado con "Cargar más").

## 9. Sistema de Filtros
Se implementó un sistema de segmentación con enfoque SaaS:
- **Ubicación:** agrupación por lugar de ejecución.
- **Tipo de licitación:** estado y tipo de contrato.
- **Presupuesto:** mínimo y máximo.
- **Fecha:** 7/30/90 días.
- **Señales IA:** recomendaciones, alta prioridad, recurrencia.

### Decisión UX relevante
Se evitó el uso de formularios largos y dropdowns extensos. En su lugar se priorizaron chips interactivos, filtros activos visibles y limpieza global en un clic.

## 10. Problemas Técnicos Encontrados y Resolución
### 10.1 GitHub Pages + LFS pointer issue
**Síntoma:** `fetch` devolvía HTTP 200 pero `response.json()` fallaba.  
**Causa raíz:** el endpoint servía punteros LFS (`version https://git-lfs.github.com/spec/v1`) en lugar de JSON real.  
**Solución:** excluir datasets frontend de LFS y mantener en LFS solo archivos históricos pesados.  
**Resultado:** endpoints frontend parseables y render estable.

### 10.2 Inconsistencias de naming (`v2` vs nombre final)
**Síntoma:** feed en 0 resultados por rutas desalineadas.  
**Causa raíz:** HTML apuntaba a un nombre distinto del archivo publicado.  
**Solución:** unificación de contrato de datos (`data/licitaciones_abiertas.json`) en scripts y frontend.

### 10.3 Dependencia runtime a datasets pesados
**Síntoma:** tiempos de carga y riesgos de fallo en cliente.  
**Causa raíz:** intentos de fetch de históricos desde frontend.  
**Solución:** desacople total; frontend usa solo dataset operativo.

## 11. Tecnologías Utilizadas
| Capa | Tecnología | Rol |
|---|---|---|
| Ingesta | Python | Scraping y parseo |
| Transformación | Python | Limpieza, filtrado y dedupe |
| Serving | JSON estático | Capa de consumo frontend |
| Frontend | HTML, TailwindCSS, JS Vanilla | Dashboard y UX |
| Visualización | Chart.js | KPIs y gráficos |
| Infraestructura | GitHub Pages | Hosting estático |
| Versionado | Git + Git LFS | Control de versiones y archivos grandes |

## 12. Resultados
### Resultado funcional
- Plataforma operativa para discovery de licitaciones abiertas y recientes.
- Carga rápida con dataset optimizado (2.000+ registros manejables en cliente).
- Filtros en tiempo real y experiencia de búsqueda fluida.

### Resultado técnico
- Arquitectura desacoplada histórico/operativo.
- Menor riesgo de errores runtime por LFS.
- Mejor mantenibilidad y trazabilidad del pipeline de datos.

### Valor diferencial
Frente a portales tradicionales, la plataforma ofrece una lectura más comercial y accionable de oportunidades, con interfaz de productividad y criterios de priorización.

## 13. Futuras Mejoras
- Scoring predictivo de adjudicación (ML supervisado).
- NLP sobre pliegos y documentos para extracción de requisitos.
- Alertas automáticas por perfiles empresariales.
- Motor de recomendación por histórico de participación.
- Módulo de competencia (benchmark de adjudicatarios).
- API privada y evolución a modelo SaaS multiempresa.

## 14. Conclusiones
Este TFM demuestra que una arquitectura Big Data ligera, bien segmentada por capas de consumo, permite convertir datos públicos complejos en una plataforma GovTech útil para negocio real.

El principal aprendizaje técnico fue que la calidad de la capa de serving (naming, LFS policy, esquema estable) condiciona directamente la fiabilidad de la UX. La separación explícita entre histórico analítico y dataset operativo no solo mejora rendimiento, sino que reduce errores de despliegue y simplifica mantenimiento.

Como resultado, se obtiene una solución académicamente sólida y técnicamente desplegable, con base suficiente para evolucionar hacia capacidades avanzadas de IA aplicada a contratación pública.

## 15. Anexo de Entrega
### Archivos relevantes del proyecto
- `scraper_contratacion.py`
- `generate_dashboard_json.py`
- `index.html`
- `data/licitaciones_abiertas.json`
- `data/licitaciones.json` (LFS)
- `data/documentos_licitacion.json` (LFS)

### Nota para revisión académica
Se recomienda incluir en versión final del TFM:
- Capturas de interfaz en producción.
- Evidencia de métricas de reducción de dataset.
- Bitácora de incidencias y resoluciones por iteración.
