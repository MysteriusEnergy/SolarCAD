# SolarCAD

Aplicación de escritorio para Windows que toma datos de una factura de electricidad LUMA, permite revisarlos y genera `datos_cliente.txt` para el comando `SOLARIMPORT6` de AutoCAD. También permite copiar 12 consumos en vertical para pegarlos en Excel. Los PDF se procesan localmente.

## Uso

1. Abre **SolarCAD** y selecciona una factura PDF.
2. Revisa o corrige los campos en **Datos para AutoCAD**. El botón **Ver avisos** muestra los valores que necesitan atención.
3. En **Consumo · Excel**, revisa los doce valores. Si no se leyeron, puedes escribirlos manualmente y copiarlos en vertical.
4. Pulsa **Generar datos_cliente.txt** y, en AutoCAD, usa `SOLARIMPORT6` para seleccionar ese archivo.

**Limpiar todo** vacía los campos antes de trabajar con otra factura. Los campos `SYSTEM_ID`, coordenadas GPS y Lambert se completan manualmente. Un campo vacío se omite en `SOLARIMPORT6`.

La gráfica LUMA contiene 13 meses; SolarCAD omite el primero para producir los 12 consumos. Algunas facturas muestran una gráfica legible para una persona pero tienen texto interno incompleto: en ese caso, comprueba la gráfica e introduce manualmente los valores que falten. No uses valores automáticos sin revisarlos.

## Ejecutar desde el código

Requiere Windows y Python 3.10 o posterior. En PowerShell, desde la carpeta del proyecto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python SolarCAD_V3.py
```

## Crear el ejecutable

```powershell
python -m pip install -r requirements-build.txt
pyinstaller --noconfirm SolarCAD.spec
```

El programa queda en `dist\SolarCAD\SolarCAD.exe`. Conserva **toda la carpeta `SolarCAD`** al copiarlo a otra computadora: el ejecutable necesita sus archivos de `_internal`. El logo de la ventana y el icono de Windows se incluyen en la compilación.

El OCR es opcional y **Tesseract no se incluye** en esta distribución. Solo se intenta automáticamente si el PDF no permite reconocer ninguna página de factura LUMA; instalar Tesseract no resuelve por sí solo las gráficas cuyo historial falla. Se necesitan los idiomas `spa` y `eng` y Tesseract accesible en `PATH` para ese caso.

## Privacidad y recursos

Las facturas de muestra están en `LUMAS/` y quedan excluidas por `.gitignore`, igual que cualquier `*.pdf` y los TXT de salida. Antes de publicar, comprueba que el repositorio no contenga documentos de clientes. El logo fuente está en `img/solarcad-logo.svg` y el PNG usado por el programa en `img/a_clean_vector_style_logo_on_a_transparent_backgro.png`.
