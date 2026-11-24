"""SolarCAD - factura LUMA a TXT compatible con SOLARIMPORT6. Python 3.10+."""

import re
import shutil
import sys
import tkinter as tk
from math import sqrt
from functools import lru_cache
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from PIL import Image, ImageTk
from pyproj import datadir, network
from pyproj.transformer import TransformerGroup

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

KEYS = [
    "PREPA_CASE",
    "SYSTEM_ID",
    "PREPA_METER",
    "LOCATION_ID",
    "NOMBRE",
    "DIRECCION",
    "PUEBLO",
    "GPS_LAT",
    "GPS_LON",
    "LAMBERT_X",
    "LAMBERT_Y",
]
LABELS = {
    "PREPA_CASE": "PREPA CASE",
    "SYSTEM_ID": "SYSTEM ID",
    "PREPA_METER": "PREPA METER",
    "LOCATION_ID": "LOCATION ID",
    "NOMBRE": "NOMBRE",
    "DIRECCION": "DIRECCION",
    "PUEBLO": "PUEBLO (ej. PR 00784)",
    "GPS_LAT": "GPS LATITUDE",
    "GPS_LON": "GPS LONGITUDE",
    "LAMBERT_X": "LAMBERT X",
    "LAMBERT_Y": "LAMBERT Y",
}

LOGO_FILE = "a_clean_vector_style_logo_on_a_transparent_backgro.png"
NAVY = "#13253F"
TEAL = "#1C728A"
GOLD = "#F5B942"
BACKGROUND = "#F3F6FA"
MUTED = "#526477"


def resource_path(*parts):
    """Encontrar recursos tanto en el codigo fuente como en PyInstaller."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).joinpath(*parts)


@lru_cache(maxsize=1)
def coordinate_transformer():
    """Usar la transformación local con rejilla; nunca cambiar a ballpark."""
    grid = resource_path("grids", "us_noaa_pvhpgn.tif")
    if not grid.is_file():
        raise RuntimeError("Falta la rejilla de Puerto Rico en la instalación de SolarCAD.")
    network.set_network_enabled(False)
    datadir.append_data_dir(str(grid.parent))
    group = TransformerGroup("EPSG:4326", "EPSG:32161", always_xy=True)
    if not group.best_available or not group.transformers or group.transformers[0].accuracy < 0:
        raise RuntimeError("No se pudo cargar la transformación NAD83 con rejilla local.")
    return group.transformers[0]


def convert_google_coordinates(value):
    """Google Maps copia latitud, longitud; PROJ recibe longitud, latitud."""
    number = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
    match = re.fullmatch(rf"\s*({number})\s*(?:[,;]|\s)\s*({number})\s*", value)
    if not match:
        raise ValueError("Pega latitud, longitud en grados decimales; ejemplo: 18.4655, -66.1057")
    latitude, longitude = map(float, match.groups())
    if not (17.5 <= latitude <= 18.7 and -68.1 <= longitude <= -64.4):
        raise ValueError("Las coordenadas están fuera de Puerto Rico/Islas Vírgenes. "
                         "Comprueba el orden: latitud, longitud.")
    x, y = coordinate_transformer().transform(longitude, latitude, errcheck=True)
    return (format(latitude, ".8f").rstrip("0").rstrip("."),
            format(longitude, ".8f").rstrip("0").rstrip("."),
            f"{x:.3f}", f"{y:.3f}")


def clean(s):
    return re.sub(r"\s+", " ", s).strip()


def normalize(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).upper()


def page_content(page, textpage=None):
    options = {"textpage": textpage} if textpage is not None else {}
    return {
        "sorted": page.get_text(sort=True, **options),
        "raw": page.get_text(**options),
        "words": page.get_text("words", **options),
    }


def luma_pages(doc, pages=None):
    """Seleccionar solo paginas con elementos propios de la factura LUMA."""
    if pages is None:
        pages = [page_content(page) for page in doc]
    texts = [normalize(p["sorted"] + " " + p["raw"]) for p in pages]
    invoice = [i for i, t in enumerate(texts) if
               "SU FACTURA DE ELECTRICIDAD" in t or
               ("SU NUMERO DE CUENTA" in t and "CONSUMO" in t) or
               ("SU CONSUMO DE ENERGIA" in t and "CANTIDAD TOTAL ADEUDADA" in t)]
    service = [i for i, t in enumerate(texts) if
               ("MEDIDOR Y DEL SERVICIO" in t or "DIRECCION DEL SERVICIO" in t or
                ("DEL MEDIDOR Y DEL SERVICIO" in t and "CONTADOR" in t))]
    history = [i for i, t in enumerate(texts) if "HISTORIAL DE CONSUMO" in t]
    if not invoice and not service:
        return []
    # La pagina de servicio puede preceder a la portada en PDFs reordenados.
    base = invoice or service
    related = [i for i in set(invoice + service + history)
               if min(abs(i - origin) for origin in base) <= 4]
    return sorted(related)


def get_match(patterns, text):
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return clean(m.group(1))
    return ""


def extract_consumption(pages, indices, warnings):
    month_re = re.compile(r"(?:ago|sep|oct|nov|dic|ene|feb|mar|abr|may|jun|jul)(?:[-–]\d{2})?", re.I)
    for idx in indices:
        content = pages[idx]
        if "HISTORIAL DE CONSUMO" not in normalize(content["raw"]):
            continue
        words = content["words"]
        header = [w for w in words if normalize(w[4]) == "HISTORIAL"]
        if not header:
            continue
        top = min(w[1] for w in header)
        month_rows = [w for w in words if month_re.fullmatch(w[4]) and top + 35 < w[1] < top + 220]
        if not month_rows:
            continue
        # La primera fila de 13 meses, antes del grafico de costos por kWh.
        min_y = min(w[1] for w in month_rows)
        months = sorted([w for w in month_rows if abs(w[1] - min_y) < 16], key=lambda w: (w[0]+w[2])/2)
        if len(months) != 13:
            # Un mes puede no aparecer en la capa de texto; los dos extremos
            # sep-XX permiten recuperar la separacion de las trece columnas.
            if not (len(months) >= 10 and months[0][4].lower().startswith("sep-")
                    and months[-1][4].lower().startswith("sep-") and
                    months[-1][0] - months[0][0] > 200):
                warnings.append("Historial: no se encontraron 13 meses verificables en la primera grafica")
                continue
            start = (months[0][0] + months[0][2]) / 2
            end = (months[-1][0] + months[-1][2]) / 2
            step = (end - start) / 12
            if any(abs((w[0] + w[2])/2 - (start + round(((w[0]+w[2])/2-start)/step)*step)) > 10
                   for w in months):
                warnings.append("Historial: columnas de meses ambiguas")
                continue
            centers = [start + i * step for i in range(13)]
            warnings.append("Historial: un mes no aparece en el texto; verificar grafica")
        else:
            centers = [(w[0] + w[2])/2 for w in months]
        month_y = min(w[1] for w in months)
        left = centers[0]
        candidates = [w for w in words if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})?|\d{4,5}", w[4])
                       and top + 8 < w[1] < month_y - 8
                       and (w[0]+w[2])/2 >= left - 12]
        used = set()
        values = []
        for cx in centers:
            nearby = sorted([(abs((w[0]+w[2])/2-cx), j, w) for j,w in enumerate(candidates)
                             if j not in used and abs((w[0]+w[2])/2-cx) < 12], key=lambda x:x[0])
            if nearby and (len(nearby) == 1 or nearby[1][0] - nearby[0][0] > 4):
                _, j, w = nearby[0]
                used.add(j)
                values.append(w[4].replace(",", "").replace(".", ""))
            else:
                values.append("")
        # Respaldo explicito: el resumen LUMA compara 'mes anterior' y 'mes actual'.
        # Solo usarlo si el ultimo consumo de la grafica coincide con el actual.
        if not values[-2] and values[-1]:
            for page_idx in indices:
                pg = pages[page_idx]
                page_text = normalize(pg["raw"])
                if "SU CONSUMO DE ENERGIA" not in page_text:
                    continue
                ws = pg["words"]
                headers = [w for w in ws if normalize(w[4]) == "CONSUMO"]
                if not headers:
                    continue
                # El resumen de LUMA presenta tres columnas: ano anterior,
                # mes anterior y mes actual. Leer por posicion X, no por orden PDF.
                yy = min(w[1] for w in headers if w[1] > 180) if any(w[1] > 180 for w in headers) else None
                if yy is None:
                    continue
                nums = sorted([w for w in ws if re.fullmatch(r"\d{2,5}", w[4])
                               and yy + 5 < w[1] < yy + 55
                               and w[0] < 220], key=lambda w: w[0])
                if len(nums) == 3 and nums[-1][4] == values[-1]:
                    values[-2] = nums[1][4]
                    warnings.append("Consumo del penultimo mes obtenido del resumen de comparacion; verificar")
                    break
        result = values[1:]
        if any(not v for v in result):
            warnings.append("Consumos sin lectura segura: " + ", ".join(str(i+1) for i,v in enumerate(result) if not v))
        return result
    warnings.append("No se pudo leer con seguridad el historial de 13 meses")
    return []


def nearby_number(words, label, width, min_digits=6):
    """Buscar un numero en la columna de una etiqueta, sin depender del orden PDF."""
    scale = width / 600
    anchors = [w for w in words if normalize(w[4]).strip(":") == label]
    for anchor in anchors:
        candidates = [w for w in words if re.fullmatch(r"\d{" + str(min_digits) + r",15}", w[4])
                      and anchor[0] - 25*scale < w[0] < anchor[2] + 155*scale
                      and -17*scale < w[1] - anchor[1] < 65*scale]
        if candidates:
            return min(candidates, key=lambda w: (abs(w[1]-anchor[1]),
                                                  abs(w[0]-anchor[2])))[4]
    return ""


def header_name(words, width, height):
    """Usar solo el encabezado de la factura, nunca nombres de anexos."""
    account = [w for w in words if normalize(w[4]).strip(":") == "CUENTA"
               and w[1] < height * .22]
    if not account:
        return ""
    anchor = account[0]
    scale = width / 600
    for word in words:
        surname_first = re.fullmatch(r"([A-ZÁÉÍÓÚÑ]{2,}),([A-ZÁÉÍÓÚÑ]{2,})", word[4])
        if not surname_first and re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{2,},", word[4]):
            following = [w for w in words if re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{2,}", w[4])
                         and abs(w[1]-word[1]) < 8*scale
                         and -2*scale <= w[0]-word[2] < 18*scale]
            if following:
                surname_first = re.fullmatch(
                    r"([A-ZÁÉÍÓÚÑ]{2,}),([A-ZÁÉÍÓÚÑ]{2,})",
                    word[4] + min(following, key=lambda w: w[0])[4])
        if not surname_first or not (anchor[1] - 52*scale < word[1] < anchor[1] + 8*scale):
            continue
        if word[0] > anchor[0] + 80*scale:
            continue
        earlier = [w for w in words if re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{2,}", w[4])
                   and abs(w[1]-word[1]) < 8*scale
                   and 0 <= word[0]-w[2] < 18*scale]
        surname = surname_first.group(1)
        if earlier:
            surname = max(earlier, key=lambda w: w[0])[4] + " " + surname
        return (surname_first.group(2) + " " + surname).upper()
    return ""


def extract_pdf(path):
    if fitz is None:
        raise RuntimeError("Instala PyMuPDF: py -m pip install pymupdf")
    data = {k: "" for k in KEYS}
    warnings = []
    with fitz.open(path) as doc:
        pages = [page_content(page) for page in doc]
        indices = luma_pages(doc, pages)
        if not indices and shutil.which("tesseract"):
            # Un PDF escaneado no tiene texto: intentar OCR local, sin enviar datos.
            try:
                pages = [page_content(page, page.get_textpage_ocr(
                    language="spa+eng", dpi=200, full=True)) for page in doc]
                indices = luma_pages(doc, pages)
                warnings.append("Se aplico OCR al PDF; verificar cada campo")
            except (RuntimeError, ValueError) as exc:
                warnings.append("OCR no disponible: " + str(exc))
        if not indices:
            warnings.append("No se identificaron paginas LUMA. Si es un escaneo, instala Tesseract OCR con idioma espanol")
            return data, [], warnings
        warnings.append("Paginas LUMA detectadas: " + ", ".join(str(i+1) for i in indices))
        sorted_pages = [pages[i]["sorted"] for i in indices]
        raw_pages = [pages[i]["raw"] for i in indices]
        text = "\n".join(sorted_pages)
        raw = "\n".join(raw_pages)
        data["PREPA_CASE"] = get_match([
            r"Su\s+n[uú]mero\s+de\s+cuenta\s*:\s*(\d{6,15})",
            r"Cuenta\s*:\s*(\d{6,15})"], text)
        data["LOCATION_ID"] = get_match([r"ID\s+localidad\s*:\s*(\d{5,15})",
                                         r"ID\s+loc\w+\s*:\s*(\d{5,15})"], text)
        invoice_indices = [i for i in indices if "SU FACTURA DE ELECTRICIDAD" in
                           normalize(pages[i]["sorted"] + pages[i]["raw"])]
        for i in invoice_indices:
            if not data["PREPA_CASE"]:
                data["PREPA_CASE"] = nearby_number(pages[i]["words"], "CUENTA", doc[i].rect.width)
            if not data["NOMBRE"]:
                data["NOMBRE"] = header_name(pages[i]["words"], doc[i].rect.width,
                                              doc[i].rect.height)
        # Direccion de servicio; separar ciudad y codigo postal del domicilio.
        for page in sorted_pages:
            m = re.search(r"Direcci.n\s+del\s+servicio\s*:\s*([^\n]+)", page, re.I)
            if not m:
                continue
            addr = clean(re.split(r"\s+ID\s+loc\w+\s*:", m.group(1), maxsplit=1,
                                  flags=re.I)[0]).upper()
            postal = re.search(r"\bPR\s*(\d{5})\b", addr)
            if postal:
                before = addr[:postal.start()].strip(" ,")
                # Ciudades PR comunes; si no se conoce, conservar direccion completa.
                towns = ("GUAYAMA", "SAN JUAN", "BAYAMON", "CAROLINA", "CAGUAS", "PONCE",
                         "MAYAGUEZ", "ARECIBO", "HUMACAO", "FAJARDO", "GUAYNABO", "DORADO",
                         "TOA BAJA", "TOA ALTA", "TRUJILLO ALTO", "RIO GRANDE", "AGUADILLA",
                         "VEGA BAJA", "VEGA ALTA", "CABO ROJO", "COAMO", "YAUCO", "MANATI",
                         "ISABELA", "HATILLO", "JUANA DIAZ", "SAN GERMAN", "SALINAS")
                matches = [town for town in towns if before.endswith(" " + town)]
                if matches:
                    town = max(matches, key=len)
                    data["DIRECCION"] = before[:-(len(town)+1)].strip(" ,")
                    data["PUEBLO"] = town + ", PR " + postal.group(1)
                else:
                    data["DIRECCION"] = before
                    data["PUEBLO"] = "PR " + postal.group(1)
                    warnings.append("Verificar ciudad de la direccion de servicio")
            else:
                data["DIRECCION"] = addr
                warnings.append("Direccion sin codigo postal PR reconocido")
            break
        # La tabla de contador se lee mejor en orden natural, no en orden visual.
        data["PREPA_METER"] = get_match([
            r"N[uú]mero\s+de\s+contador[\s\S]{0,350}?\bD[ií]as\s*\n\s*(\d{6,15})\b",
            r"N[uú]mero\s+de\s+contador[\s\S]{0,350}?\b(\d{7,12})\s*\n\s*\d{1,2}[-/]\w+"], raw)
        if not data["PREPA_METER"]:
            for i in indices:
                if "MEDIDOR Y DEL SERVICIO" not in normalize(pages[i]["raw"]):
                    continue
                words = pages[i]["words"]
                anchors = [w for w in words if normalize(w[4]) == "CONTADOR"]
                for anchor in anchors:
                    scale = doc[i].rect.width / 600
                    candidates = [w for w in words if re.fullmatch(r"\d{6,15}", w[4])
                                  and anchor[0]-35*scale < w[0] < anchor[2]+75*scale
                                  and 5*scale < w[1]-anchor[1] < 65*scale]
                    if candidates:
                        data["PREPA_METER"] = min(candidates,
                                                  key=lambda w: (w[1]-anchor[1],
                                                                 abs(w[0]-anchor[0])))[4]
                        break
                if data["PREPA_METER"]:
                    break
        consumption = extract_consumption(pages, indices, warnings)
        for k in ("PREPA_CASE", "PREPA_METER", "LOCATION_ID", "NOMBRE", "DIRECCION", "PUEBLO"):
            if not data[k]:
                warnings.append("Revisar " + LABELS[k] + ": no detectado")
        return data, consumption, warnings


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SolarCAD | Facturas LUMA")
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        # Abrir con margen alrededor, sin ocupar casi toda la altura de la pantalla.
        # En pantallas HiDPI compensar la escala de Tk para no quedar diminuto.
        dpi_factor = max(1.0, min(2.0, float(self.tk.call("tk", "scaling")) / (4/3)))
        usable_w, usable_h = max(1, screen_w-60), max(1, screen_h-90)
        initial_w = min(usable_w, round(960*dpi_factor),
                        max(min(560, usable_w), round(screen_w*.70)))
        initial_h = min(usable_h, round(760*dpi_factor),
                        max(min(480, usable_h), round(screen_h*.73)))
        self.geometry(f"{initial_w}x{initial_h}+{(screen_w-initial_w)//2}"
                      f"+{(screen_h-initial_h)//2}")
        self.minsize(min(560, usable_w), min(480, usable_h))
        self.configure(background=BACKGROUND)
        self.vars = {k: tk.StringVar() for k in KEYS}
        self.months = [tk.StringVar() for _ in range(12)]
        self.google_coords = tk.StringVar()
        self.coordinate_hint = tk.StringVar(value="Google Maps · latitud, longitud (ej.: 18.4655, -66.1057)")
        self.pdf_path = None
        self.preview_page = 0
        self.preview_total = 0
        self.preview_history_pages = set()
        self.preview_zoom = 1.0
        self.preview_image = None
        self.preview_placeholder = None
        self.preview_job = None
        self.preview_side_by_side = None
        self.preview_page_label = tk.StringVar(value="Abre una factura para ver su gráfica")
        self.status = tk.StringVar(value="Listo para empezar. Selecciona una factura LUMA en PDF.")
        self.notice = tk.StringVar(value="Los datos se procesan en este equipo. Revisa cada campo antes de exportar.")
        self.warnings = []
        self.configure_styles()

        banner = tk.Frame(self, bg=NAVY, padx=26, pady=10)
        banner.pack(fill="x")
        self.load_logo(banner)
        branding = tk.Frame(banner, bg=NAVY)
        branding.pack(side="left", padx=(16, 0))
        tk.Label(branding, text="SolarCAD", bg=NAVY, fg="white",
                 font=("Segoe UI", 24, "bold")).pack(anchor="w")
        tk.Label(branding, text="De tu factura LUMA a tus datos de trabajo",
                 bg=NAVY, fg="#C7D5E3", font=("Segoe UI", 10)).pack(anchor="w")
        self.banner_badge = tk.Label(banner, text="LOCAL  •  PDF → TXT", bg=NAVY, fg=GOLD,
                                     font=("Segoe UI", 10, "bold"))
        self.banner_badge.pack(side="right", padx=5)
        banner.bind("<Configure>", self.resize_banner)

        outer = ttk.Frame(self, style="App.TFrame", padding=(24, 12, 24, 12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        body = ttk.Frame(outer, style="App.TFrame")
        body.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(body, background=BACKGROUND, borderwidth=0,
                                highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(self.canvas, style="App.TFrame", padding=(0, 0, 6, 0))
        self.content_window = self.canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", self.update_scroll_region)
        self.canvas.bind("<Configure>", self.resize_content)
        self.bind_all("<MouseWheel>", self.scroll_with_wheel, add="+")

        file_card = ttk.Frame(content, style="Card.TFrame", padding=(18, 8))
        file_card.pack(fill="x")
        self.open_button = ttk.Button(file_card, text="Abrir factura PDF", style="Accent.TButton",
                                      command=self.open_pdf, cursor="hand2")
        self.open_button.pack(side="left")
        file_info = ttk.Frame(file_card, style="Card.TFrame")
        file_info.pack(side="left", fill="x", expand=True, padx=(18, 0))
        ttk.Label(file_info, text="ARCHIVO ACTUAL", style="Caption.TLabel").pack(anchor="w")
        self.filename = ttk.Label(file_info, text="Ningún archivo seleccionado",
                                  style="Card.TLabel")
        self.filename.pack(anchor="w")

        notebook = ttk.Notebook(content, style="Main.TNotebook")
        notebook.pack(fill="both", expand=True, pady=(14, 12))
        notebook.bind("<<NotebookTabChanged>>", self.change_tab)
        tab = ttk.Frame(notebook, style="Page.TFrame", padding=(22, 14))
        notebook.add(tab, text="Datos para AutoCAD")
        ttk.Label(tab, text="Datos de la factura", style="Section.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Los campos detectados se pueden corregir manualmente.",
                  style="Hint.TLabel").pack(anchor="w", pady=(1, 9))
        self.field_group(tab, ("PREPA_CASE", "PREPA_METER", "LOCATION_ID",
                               "NOMBRE", "DIRECCION", "PUEBLO"))
        ttk.Separator(tab).pack(fill="x", pady=(12, 9))
        ttk.Label(tab, text="Datos adicionales", style="Section.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Completa estos datos si los necesitas en tu proyecto.",
                  style="Hint.TLabel").pack(anchor="w", pady=(1, 9))
        self.field_group(tab, ("SYSTEM_ID", "GPS_LAT", "GPS_LON", "LAMBERT_X", "LAMBERT_Y"))
        coordinates = ttk.Frame(tab, style="Coordinates.TFrame", padding=(14, 10))
        coordinates.pack(fill="x", pady=(12, 0))
        ttk.Label(coordinates, text="Convertir desde Google Maps", style="CoordinatesTitle.TLabel").pack(anchor="w")
        coordinates_row = ttk.Frame(coordinates, style="Coordinates.TFrame")
        coordinates_row.pack(fill="x", pady=(7, 0))
        self.google_entry = ttk.Entry(coordinates_row, textvariable=self.google_coords,
                                      style="Field.TEntry")
        self.google_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.google_entry.bind("<Return>", lambda _event: self.convert_coordinates())
        self.convert_button = ttk.Button(coordinates_row, text="Convertir", style="Accent.TButton",
                                         command=self.convert_coordinates, cursor="hand2")
        self.convert_button.pack(side="right")
        self.coordinate_hint_label = ttk.Label(coordinates, textvariable=self.coordinate_hint,
                                               style="CoordinatesHint.TLabel")
        self.coordinate_hint_label.pack(anchor="w", pady=(5, 0))

        tab2 = ttk.Frame(notebook, style="Page.TFrame", padding=(22, 18))
        notebook.add(tab2, text="Consumo · Excel")
        ttk.Label(tab2, text="Historial de consumo", style="Section.TLabel").pack(anchor="w")
        self.consumption_hint = ttk.Label(
            tab2, text="12 valores para pegar en Excel. Se omite el primer mes de la gráfica de 13. "
                       "Si la lectura automática falla, escribe los valores aquí.",
            style="Hint.TLabel", wraplength=780)
        self.consumption_hint.pack(anchor="w", pady=(4, 18))
        tab2.bind("<Configure>", lambda event: self.consumption_hint.configure(
            wraplength=max(220, event.width-44)))
        self.consumption_workspace = ttk.Frame(tab2, style="Page.TFrame")
        self.consumption_workspace.pack(fill="both", expand=True)
        self.preview_panel = ttk.Frame(self.consumption_workspace, style="Page.TFrame")
        self.values_panel = ttk.Frame(self.consumption_workspace, style="Page.TFrame")
        self.consumption_workspace.bind("<Configure>", self.resize_consumption_workspace)

        ttk.Label(self.preview_panel, text="Factura original", style="Section.TLabel").pack(anchor="w")
        page_controls = ttk.Frame(self.preview_panel, style="Page.TFrame")
        page_controls.pack(fill="x", pady=(6, 5))
        self.previous_page_button = ttk.Button(page_controls, text="◀", style="Quiet.TButton",
                                               command=lambda: self.change_preview_page(-1),
                                               cursor="hand2", state="disabled", width=3)
        self.previous_page_button.pack(side="left")
        self.next_page_button = ttk.Button(page_controls, text="▶", style="Quiet.TButton",
                                           command=lambda: self.change_preview_page(1),
                                           cursor="hand2", state="disabled", width=3)
        self.next_page_button.pack(side="left", padx=(5, 0))
        ttk.Label(page_controls, textvariable=self.preview_page_label,
                  style="Page.TLabel").pack(side="left", padx=(10, 0))
        zoom_controls = ttk.Frame(self.preview_panel, style="Page.TFrame")
        zoom_controls.pack(fill="x", pady=(0, 7))
        self.preview_zoom_buttons = []
        for text, command in (("−", lambda: self.change_preview_zoom(1 / 1.4)),
                              ("+", lambda: self.change_preview_zoom(1.4)),
                              ("Ajustar ancho", self.reset_preview_zoom)):
            button = ttk.Button(zoom_controls, text=text, style="Quiet.TButton",
                                command=command, cursor="hand2", state="disabled")
            button.pack(side="left", padx=(0, 5))
            self.preview_zoom_buttons.append(button)

        preview_area = ttk.Frame(self.preview_panel, style="Page.TFrame")
        preview_area.pack(fill="both", expand=True)
        preview_area.columnconfigure(0, weight=1)
        preview_area.rowconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(preview_area, bg="#E8EEF4", width=310, height=320,
                                        highlightthickness=0, borderwidth=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        preview_vscroll = ttk.Scrollbar(preview_area, orient="vertical",
                                        command=self.preview_canvas.yview)
        preview_vscroll.grid(row=0, column=1, sticky="ns")
        preview_hscroll = ttk.Scrollbar(preview_area, orient="horizontal",
                                        command=self.preview_canvas.xview)
        preview_hscroll.grid(row=1, column=0, sticky="ew")
        self.preview_canvas.configure(yscrollincrement=24)
        self.preview_canvas.configure(yscrollcommand=preview_vscroll.set,
                                      xscrollcommand=preview_hscroll.set)
        self.preview_canvas.bind("<Configure>", self.preview_resized)
        self.show_preview_placeholder("Abre una factura PDF para verla aquí")
        preview_help = ttk.Label(
            self.preview_panel,
            text="Rueda sobre la imagen: mover página · Mayús + rueda: mover a los lados",
            style="Hint.TLabel", wraplength=520)
        preview_help.pack(anchor="w", pady=(5, 0))
        self.preview_panel.bind("<Configure>", lambda event: preview_help.configure(
            wraplength=max(220, event.width-20)))

        ttk.Label(self.values_panel, text="12 consumos (kWh)", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.values_panel, text="Verifica cada valor en la gráfica.",
                  style="Hint.TLabel").pack(anchor="w", pady=(3, 9))
        grid = ttk.Frame(self.values_panel, style="Page.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for i, var in enumerate(self.months):
            ttk.Label(grid, text=f"Mes {i+1:02d}", style="Page.TLabel").grid(
                row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            ttk.Entry(grid, textvariable=var, style="Field.TEntry", width=12).grid(
                row=i, column=1, sticky="ew", pady=4)
        ttk.Button(self.values_panel, text="Copiar 12 valores para Excel",
                   style="Secondary.TButton", command=self.copy_consumption,
                   cursor="hand2").pack(anchor="w", pady=(14, 0))

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(2, weight=1)
        self.export_button = ttk.Button(actions, text="Generar datos_cliente.txt",
                                        style="Primary.TButton", command=self.export, cursor="hand2")
        self.export_button.grid(row=0, column=0, sticky="w")
        self.clear_button = ttk.Button(actions, text="Limpiar todo", style="Quiet.TButton",
                                       command=self.clear_all, cursor="hand2")
        self.clear_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.review_button = ttk.Button(actions, text="Ver avisos", style="Quiet.TButton",
                                         command=self.review_warnings, state="disabled", cursor="hand2")
        self.review_button.grid(row=0, column=2, sticky="e")
        self.narrow_actions = False
        actions.bind("<Configure>", self.resize_actions)

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Status.TLabel",
                                      wraplength=850)
        self.status_label.pack(anchor="w")
        self.notice_label = ttk.Label(footer, textvariable=self.notice, style="Footer.TLabel",
                                      wraplength=850)
        self.notice_label.pack(anchor="w", pady=(2, 0))
        outer.bind("<Configure>", self.resize_footer)

    def update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def resize_content(self, event):
        self.canvas.itemconfigure(self.content_window, width=event.width)
        self.filename.configure(wraplength=max(170, event.width-230))
        self.update_scroll_region()

    def resize_actions(self, event):
        narrow = event.width < 700
        if narrow == self.narrow_actions:
            return
        self.narrow_actions = narrow
        if narrow:
            self.review_button.grid_configure(row=1, column=0, columnspan=3,
                                              sticky="e", pady=(6, 0))
        else:
            self.review_button.grid_configure(row=0, column=2, columnspan=1,
                                              sticky="e", pady=0)

    def resize_footer(self, event):
        wrap = max(260, event.width - 55)
        self.status_label.configure(wraplength=wrap)
        self.notice_label.configure(wraplength=wrap)

    def resize_banner(self, event):
        if event.width < 760 and self.banner_badge.winfo_manager():
            self.banner_badge.pack_forget()
        elif event.width >= 760 and not self.banner_badge.winfo_manager():
            self.banner_badge.pack(side="right", padx=5)

    def change_tab(self, event):
        self.canvas.yview_moveto(0)
        if event.widget.index("current") == 1:
            self.after_idle(self.schedule_preview)

    def resize_consumption_workspace(self, event):
        side_by_side = event.width >= 750
        if side_by_side == self.preview_side_by_side:
            return
        self.preview_side_by_side = side_by_side
        self.preview_panel.grid_forget()
        self.values_panel.grid_forget()
        self.consumption_workspace.columnconfigure(0, weight=3 if side_by_side else 1)
        self.consumption_workspace.columnconfigure(1, weight=2 if side_by_side else 0)
        if side_by_side:
            self.preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
            self.values_panel.grid(row=0, column=1, sticky="nsew")
        else:
            self.preview_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 18))
            self.values_panel.grid(row=1, column=0, sticky="nsew")

    def show_preview_placeholder(self, text):
        self.preview_image = None
        self.preview_canvas.delete("all")
        width = max(self.preview_canvas.winfo_width(), 1)
        height = max(self.preview_canvas.winfo_height(), 1)
        self.preview_placeholder = self.preview_canvas.create_text(
            width/2, height/2, text=text, fill=MUTED,
            font=("Segoe UI", 10), width=max(160, width-35), justify="center")
        self.preview_canvas.configure(scrollregion=(0, 0, width, height))
        self.preview_canvas.xview_moveto(0)
        self.preview_canvas.yview_moveto(0)

    def load_preview(self, path):
        self.clear_preview()
        try:
            with fitz.open(path) as doc:
                self.preview_total = len(doc)
                self.preview_history_pages = {
                    i for i, page in enumerate(doc)
                    if "HISTORIAL DE CONSUMO" in normalize(page.get_text())
                }
                indices = luma_pages(doc)
        except Exception as exc:
            self.show_preview_placeholder("No se pudo mostrar el PDF: " + str(exc))
            return
        if not self.preview_total:
            self.show_preview_placeholder("El PDF no contiene páginas")
            return
        self.pdf_path = Path(path)
        self.preview_page = (min(self.preview_history_pages)
                             if self.preview_history_pages else (indices[-1] if indices else 0))
        self.update_preview_controls()
        self.schedule_preview()

    def update_preview_controls(self):
        if not self.preview_total:
            self.preview_page_label.set("Abre una factura para ver su gráfica")
        else:
            label = f"Página {self.preview_page+1} de {self.preview_total}"
            if self.preview_page in self.preview_history_pages:
                label += " · Historial"
            self.preview_page_label.set(label)
        self.previous_page_button.configure(
            state="normal" if self.preview_total and self.preview_page > 0 else "disabled")
        self.next_page_button.configure(
            state="normal" if self.preview_page+1 < self.preview_total else "disabled")
        for button in self.preview_zoom_buttons:
            button.configure(state="normal" if self.preview_total else "disabled")

    def change_preview_page(self, step):
        if not self.preview_total:
            return
        self.preview_page = max(0, min(self.preview_total-1, self.preview_page+step))
        self.update_preview_controls()
        self.schedule_preview()

    def change_preview_zoom(self, factor):
        if self.preview_total:
            self.preview_zoom = max(0.7, min(4.0, self.preview_zoom * factor))
            self.schedule_preview()

    def reset_preview_zoom(self):
        if self.preview_total:
            self.preview_zoom = 1.0
            self.schedule_preview()

    def preview_resized(self, event):
        if self.preview_placeholder is not None:
            self.preview_canvas.coords(self.preview_placeholder, event.width/2, event.height/2)
            self.preview_canvas.itemconfigure(self.preview_placeholder,
                                              width=max(160, event.width-35))
            self.preview_canvas.configure(scrollregion=(0, 0, event.width, event.height))
        if event.width != getattr(self, "preview_width", None):
            self.preview_width = event.width
            self.schedule_preview()

    def schedule_preview(self):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if self.pdf_path is not None:
            self.preview_job = self.after(130, self.render_preview)

    def render_preview(self):
        self.preview_job = None
        if self.pdf_path is None or self.preview_canvas.winfo_width() < 80:
            return
        try:
            with fitz.open(self.pdf_path) as doc:
                page = doc[self.preview_page]
                width, height = page.rect.width, page.rect.height
                fit_width = max(100, self.preview_canvas.winfo_width()-18) / width
                scale = min(fit_width * self.preview_zoom, 2.5,
                            sqrt(5_000_000 / (width * height)))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                                         colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            old_x, old_y = self.preview_canvas.xview()[0], self.preview_canvas.yview()[0]
            self.preview_image = ImageTk.PhotoImage(image)
            self.preview_canvas.delete("all")
            self.preview_placeholder = None
            x = max(0, (self.preview_canvas.winfo_width() - image.width)//2)
            self.preview_canvas.create_image(x, 0, anchor="nw", image=self.preview_image)
            self.preview_canvas.configure(scrollregion=(0, 0,
                max(self.preview_canvas.winfo_width(), x+image.width),
                max(self.preview_canvas.winfo_height(), image.height)))
            self.preview_canvas.xview_moveto(old_x)
            self.preview_canvas.yview_moveto(old_y)
        except Exception as exc:
            self.show_preview_placeholder("No se pudo mostrar esta página: " + str(exc))

    def clear_preview(self):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        self.pdf_path = None
        self.preview_total = 0
        self.preview_page = 0
        self.preview_zoom = 1.0
        self.preview_history_pages = set()
        self.update_preview_controls()
        self.show_preview_placeholder("Abre una factura PDF para verla aquí")

    def scroll_with_wheel(self, event):
        if not self.canvas.winfo_exists() or event.widget.winfo_toplevel() != self:
            return
        px, py = self.preview_canvas.winfo_rootx(), self.preview_canvas.winfo_rooty()
        if (self.preview_canvas.winfo_ismapped() and
            px <= event.x_root < px+self.preview_canvas.winfo_width() and
                py <= event.y_root < py+self.preview_canvas.winfo_height()):
            steps = 3 * max(1, abs(event.delta) // 120)
            if event.state & 0x0001:
                self.preview_canvas.xview_scroll(-steps if event.delta > 0 else steps, "units")
            else:
                self.preview_canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
            return "break"
        x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        if x <= event.x_root < x+self.canvas.winfo_width() and y <= event.y_root < y+self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background="white")
        style.configure("Page.TFrame", background="white")
        style.configure("Coordinates.TFrame", background="#EDF4F8")
        style.configure("CoordinatesTitle.TLabel", background="#EDF4F8", foreground=NAVY,
                        font=("Segoe UI", 10, "bold"))
        style.configure("CoordinatesHint.TLabel", background="#EDF4F8", foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("CoordinatesError.TLabel", background="#EDF4F8", foreground="#AE3030",
                        font=("Segoe UI", 9, "bold"))
        style.configure("Card.TLabel", background="white", foreground=NAVY,
                        font=("Segoe UI", 11, "bold"))
        style.configure("Caption.TLabel", background="white", foreground=TEAL,
                        font=("Segoe UI", 9, "bold"))
        style.configure("Page.TLabel", background="white", foreground=NAVY,
                        font=("Segoe UI", 10))
        style.configure("Section.TLabel", background="white", foreground=NAVY,
                        font=("Segoe UI", 12, "bold"))
        style.configure("Hint.TLabel", background="white", foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Footer.TLabel", background=BACKGROUND, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=BACKGROUND, foreground=NAVY,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Success.TLabel", background=BACKGROUND, foreground="#196B51",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Error.TLabel", background=BACKGROUND, foreground="#AE3030",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Field.TEntry", padding=(9, 3), fieldbackground="white",
                        foreground=NAVY, bordercolor="#C8D4DF", font=("Segoe UI", 10))
        style.map("Field.TEntry", bordercolor=[("focus", TEAL)])
        style.configure("Main.TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("Main.TNotebook.Tab", background="#E5EBF2", foreground=MUTED,
                        padding=(18, 9), font=("Segoe UI", 10, "bold"))
        style.map("Main.TNotebook.Tab", background=[("selected", "white")],
                  foreground=[("selected", NAVY)])
        for name, bg, fg, hover, pressed in (
            ("Accent", GOLD, NAVY, "#FFD477", "#DDA52B"),
            ("Primary", NAVY, "white", "#284D71", "#0C1C31"),
            ("Secondary", TEAL, "white", "#258CA8", "#14546A"),
            ("Quiet", "#E5EBF2", NAVY, "#D2E1ED", "#B8CCDD"),
        ):
            style.configure(name + ".TButton", background=bg, foreground=fg,
                            padding=(15, 9), borderwidth=1, relief="flat",
                            font=("Segoe UI", 10, "bold"))
            style.map(name + ".TButton",
                      background=[("disabled", "#DDE3E9"), ("pressed", pressed),
                                  ("active", hover)],
                      relief=[("pressed", "sunken"), ("active", "raised")],
                      foreground=[("disabled", "#8796A5")])

    def load_logo(self, banner):
        path = resource_path("img", LOGO_FILE)
        if not path.is_file():
            return
        with Image.open(path) as image:
            logo = image.convert("RGBA")
        self.window_icon = ImageTk.PhotoImage(logo.resize((64, 64), Image.Resampling.LANCZOS))
        self.iconphoto(True, self.window_icon)
        self.header_logo = ImageTk.PhotoImage(logo.resize((76, 76), Image.Resampling.LANCZOS))
        tk.Label(banner, image=self.header_logo, bg=NAVY).pack(side="left")

    def field_group(self, parent, fields):
        group = ttk.Frame(parent, style="Page.TFrame")
        group.pack(fill="x")
        group.columnconfigure(1, weight=1)
        for row, key in enumerate(fields):
            ttk.Label(group, text=LABELS[key], style="Page.TLabel", width=23).grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Entry(group, textvariable=self.vars[key], style="Field.TEntry").grid(
                row=row, column=1, sticky="ew", pady=2)

    def review_warnings(self):
        if self.warnings:
            messagebox.showwarning("Avisos de la factura", "\n".join(self.warnings))

    def set_feedback(self, message, kind="normal"):
        self.status.set(message)
        self.status_label.configure(style={"success": "Success.TLabel",
                                           "error": "Error.TLabel"}.get(kind, "Status.TLabel"))

    def convert_coordinates(self):
        try:
            latitude, longitude, x, y = convert_google_coordinates(self.google_coords.get())
        except (ValueError, RuntimeError) as exc:
            self.coordinate_hint.set(str(exc))
            self.coordinate_hint_label.configure(style="CoordinatesError.TLabel")
            self.set_feedback("No se convirtieron las coordenadas. Corrige la entrada.", "error")
            return
        for key, value in zip(("GPS_LAT", "GPS_LON", "LAMBERT_X", "LAMBERT_Y"),
                              (latitude, longitude, x, y)):
            self.vars[key].set(value)
        self.coordinate_hint.set("Conversión local completada · NAD83 / Puerto Rico e Islas Vírgenes · metros")
        self.coordinate_hint_label.configure(style="CoordinatesHint.TLabel")
        self.set_feedback("Coordenadas convertidas: revisa GPS y Lambert antes de exportar.", "success")

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("Facturas PDF", "*.pdf")])
        if not path:
            return
        self.open_button.configure(state="disabled")
        self.set_feedback("Leyendo factura PDF…")
        self.update_idletasks()
        try:
            data, cons, warnings = extract_pdf(path)
        except Exception as e:
            self.set_feedback("No se pudo abrir la factura.", "error")
            messagebox.showerror("Error al abrir PDF", str(e))
            return
        finally:
            self.open_button.configure(state="normal")
        for k in KEYS:
            self.vars[k].set(data[k])
        self.google_coords.set("")
        self.coordinate_hint.set("Google Maps · latitud, longitud (ej.: 18.4655, -66.1057)")
        self.coordinate_hint_label.configure(style="CoordinatesHint.TLabel")
        for i, v in enumerate(self.months):
            v.set(cons[i] if len(cons) == 12 else "")
        self.filename.config(text=Path(path).name)
        count = sum(bool(data[k]) for k in ("PREPA_CASE", "PREPA_METER", "LOCATION_ID",
                                            "NOMBRE", "DIRECCION", "PUEBLO"))
        self.set_feedback(f"Factura cargada · {count} de 6 datos principales · "
                          f"{sum(bool(v) for v in cons)} de 12 consumos", "success")
        self.warnings = [warning for warning in warnings
                         if not warning.startswith("Paginas LUMA detectadas:")]
        self.notice.set("Revisa los campos y los consumos antes de exportar. "
                        + ("Hay avisos disponibles." if self.warnings else ""))
        self.review_button.configure(state="normal" if self.warnings else "disabled")
        self.load_preview(path)

    def clear_all(self):
        self.clear_preview()
        for var in self.vars.values():
            var.set("")
        for var in self.months:
            var.set("")
        self.google_coords.set("")
        self.coordinate_hint.set("Google Maps · latitud, longitud (ej.: 18.4655, -66.1057)")
        self.coordinate_hint_label.configure(style="CoordinatesHint.TLabel")
        self.filename.config(text="Ningún archivo seleccionado")
        self.set_feedback("Formulario limpio. Selecciona una factura PDF.", "success")
        self.notice.set("Los datos se procesan en este equipo. Revisa cada campo antes de exportar.")
        self.warnings = []
        self.review_button.configure(state="disabled")

    def export(self):
        data = {k: self.vars[k].get().strip() for k in KEYS}
        if not data["PREPA_CASE"]:
            if not messagebox.askyesno(
                "Sin PREPA CASE", "No hay PREPA CASE. ¿Generar TXT de todos modos?"
            ):
                self.set_feedback("Exportación cancelada: falta PREPA CASE.", "error")
                return
        if any(("\n" in v or "\r" in v or "=" in v) for v in data.values()):
            self.set_feedback("Corrige los campos inválidos antes de exportar.", "error")
            messagebox.showerror(
                "Valor inválido",
                "Los campos no deben contener saltos de línea ni el carácter =.",
            )
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile="datos_cliente.txt",
            filetypes=[("Archivo TXT", "*.txt")],
        )
        if not path:
            return
        try:
            Path(path).write_text(
                "\n".join(f"{k}={data[k]}" for k in KEYS) + "\n", encoding="utf-8-sig"
            )
        except Exception as e:
            self.set_feedback("No se pudo guardar el TXT.", "error")
            messagebox.showerror("Error al guardar", str(e))
            return
        self.set_feedback(
            "TXT generado: "
            + path
            + " | En AutoCAD usa SOLARIMPORT6 y selecciona este archivo.", "success"
        )
        messagebox.showinfo(
            "TXT listo",
            "Archivo guardado. En AutoCAD ejecuta SOLARIMPORT6 y selecciona el TXT.",
        )

    def copy_consumption(self):
        vals = [v.get().strip() for v in self.months]
        if not all(re.fullmatch(r"\d+", v) for v in vals):
            self.set_feedback("Completa los 12 consumos antes de copiarlos.", "error")
            messagebox.showwarning(
                "Consumo incompleto",
                "Debes revisar/completar los 12 valores numéricos antes de copiarlos.",
            )
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(vals))
        self.update()
        self.set_feedback("12 consumos copiados verticalmente. Pégalos en Excel con Ctrl+V.",
                          "success")


if __name__ == "__main__":
    if "--verify-installation" in sys.argv:
        # Tambien prueba los recursos incluidos al ejecutarlo desde PyInstaller.
        try:
            lat, lon, x, y = convert_google_coordinates("18.4655, -66.1057")
            if (lat, lon) != ("18.4655", "-66.1057") or abs(float(x)-234606.185) > .01 or abs(float(y)-269999.539) > .01:
                raise RuntimeError("La transformación de coordenadas no coincide con la rejilla local.")
            app = App()
            app.withdraw()
            app.update_idletasks()
            app.destroy()
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0)
    App().mainloop()
