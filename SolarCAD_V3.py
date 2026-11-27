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


def unwrap_coordinates(value):
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        return value[1:-1].strip()
    return value


def convert_google_coordinates(value):
    """Google Maps copia latitud, longitud; PROJ recibe longitud, latitud."""
    number = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
    match = re.fullmatch(rf"\s*({number})\s*(?:[,;]|\s)\s*({number})\s*", unwrap_coordinates(value))
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
        # Rótulo es compacto; reservar más altura solo si el usuario la pide.
        initial_h = min(usable_h, round(665*dpi_factor),
                        max(min(480, usable_h), round(screen_h*.82)))
        self.compact_height = initial_h
        self.consumption_height = min(usable_h, round(730*dpi_factor))
        self.consumption_window = None
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

        banner = tk.Frame(self, bg=NAVY, padx=16, pady=7)
        banner.pack(fill="x")
        self.load_logo(banner)
        tk.Label(banner, text="SolarCAD", bg=NAVY, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=(8, 14))
        self.open_button = ttk.Button(banner, text="Abrir PDF", style="Accent.TButton",
                                      command=self.open_pdf, cursor="hand2")
        self.open_button.pack(side="right")
        self.filename = tk.Label(banner, text="Ningún archivo seleccionado", bg=NAVY,
                                 fg="#C7D5E3", anchor="w", font=("Segoe UI", 9))
        self.filename.pack(side="left", fill="x", expand=True, padx=(0, 8))

        outer = ttk.Frame(self, style="App.TFrame", padding=(14, 8))
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

        notebook = ttk.Notebook(content, style="Main.TNotebook")
        self.notebook = notebook
        notebook.pack(fill="both", expand=True)
        notebook.enable_traversal()
        notebook.bind("<<NotebookTabChanged>>", self.change_tab)
        self.field_entries = {}
        self.title_tab = ttk.Frame(notebook, style="App.TFrame", padding=(10, 6))
        notebook.add(self.title_tab, text="Rótulo", underline=0)
        client = self.form_card(self.title_tab, 0, "Cliente y servicio", TEAL)
        location = self.form_card(self.title_tab, 1, "Ubicación", GOLD)
        self.field_group(client, (("NOMBRE", "PUEBLO"), ("DIRECCION",),
                                  ("PREPA_CASE", "PREPA_METER"),
                                  ("LOCATION_ID", "SYSTEM_ID")), tab=self.title_tab)
        coordinates = ttk.Frame(location, style="Coordinates.TFrame", padding=(8, 5))
        coordinates.pack(fill="x", pady=(5, 0))
        ttk.Label(coordinates, text="Pegar las coordenadas de Site Capture",
                  style="CoordinatesTitle.TLabel").pack(anchor="w")
        coordinates_row = ttk.Frame(coordinates, style="Coordinates.TFrame")
        coordinates_row.pack(fill="x", pady=(3, 0))
        self.google_entry = ttk.Entry(coordinates_row, textvariable=self.google_coords,
                                      style="Field.TEntry", width=9)
        self.google_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.google_entry.bind("<Return>", lambda _event: self.convert_coordinates())
        self.convert_button = ttk.Button(coordinates_row, text="Convertir", style="Accent.TButton",
                                         command=self.convert_coordinates, cursor="hand2", padding=(8, 5))
        self.convert_button.pack(side="right")
        self.coordinate_hint_label = ttk.Label(coordinates, textvariable=self.coordinate_hint,
                                               style="CoordinatesHint.TLabel")
        self.coordinate_hint_label.pack(anchor="w", pady=(3, 0))
        coordinates.bind("<Configure>", lambda event: self.coordinate_hint_label.configure(
            wraplength=max(1, event.width-18)))
        self.field_group(location, (("GPS_LAT", "GPS_LON", "LAMBERT_X", "LAMBERT_Y"),),
                         tab=self.title_tab)
        ttk.Label(location, text="NAD83 · Lambert X/Y en metros", justify="left",
                  style="Hint.TLabel").pack(anchor="w", pady=(3, 0))

        tab2 = ttk.Frame(notebook, style="Page.TFrame", padding=(16, 10))
        self.consumption_tab = tab2
        notebook.add(tab2, text="Consumo", underline=0)
        self.consumption_workspace = ttk.Frame(tab2, style="Page.TFrame")
        self.consumption_workspace.pack(fill="both", expand=True)
        self.consumption_workspace.rowconfigure(0, weight=1)
        self.preview_panel = tk.Frame(self.consumption_workspace, bg="white", padx=4, pady=4)
        self.preview_detached = False
        self.preview_close_callback = self.register(self.close_preview_window)
        self.values_panel = ttk.Frame(self.consumption_workspace, style="Page.TFrame")
        self.consumption_workspace.bind("<Configure>", self.resize_consumption_workspace)

        page_controls = ttk.Frame(self.preview_panel, style="Page.TFrame")
        page_controls.pack(fill="x", pady=(3, 4))
        self.previous_page_button = ttk.Button(page_controls, text="◀", style="Quiet.TButton",
                                               command=lambda: self.change_preview_page(-1),
                                               cursor="hand2", state="disabled", width=3, padding=(8, 4))
        self.previous_page_button.pack(side="left")
        self.next_page_button = ttk.Button(page_controls, text="▶", style="Quiet.TButton",
                                           command=lambda: self.change_preview_page(1),
                                           cursor="hand2", state="disabled", width=3, padding=(8, 4))
        self.next_page_button.pack(side="left", padx=(5, 0))
        ttk.Label(page_controls, textvariable=self.preview_page_label,
                  style="Page.TLabel").pack(side="left", padx=(10, 0))
        zoom_controls = ttk.Frame(self.preview_panel, style="Page.TFrame")
        zoom_controls.pack(fill="x", pady=(0, 4))
        self.preview_zoom_buttons = []
        for text, command in (("−", lambda: self.change_preview_zoom(1 / 1.4)),
                              ("+", lambda: self.change_preview_zoom(1.4)),
                              ("Ajustar ancho", self.reset_preview_zoom)):
            button = ttk.Button(zoom_controls, text=text, style="Quiet.TButton",
                                command=command, cursor="hand2", state="disabled", padding=(9, 4))
            button.pack(side="left", padx=(0, 5))
            self.preview_zoom_buttons.append(button)

        preview_area = ttk.Frame(self.preview_panel, style="Page.TFrame")
        preview_area.pack(fill="both", expand=True)
        preview_area.columnconfigure(0, weight=1)
        preview_area.rowconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(preview_area, bg="#E8EEF4", width=280, height=185,
                                        highlightthickness=0, borderwidth=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        preview_vscroll = ttk.Scrollbar(preview_area, orient="vertical",
                                        command=self.preview_canvas.yview)
        preview_vscroll.grid(row=0, column=1, sticky="ns")
        preview_hscroll = ttk.Scrollbar(preview_area, orient="horizontal",
                                        command=self.preview_canvas.xview)
        preview_hscroll.grid(row=1, column=0, sticky="ew")
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
        ttk.Label(self.values_panel, text="Omite el primero de la gráfica de 13 meses.",
                  style="Hint.TLabel", wraplength=240).pack(anchor="w", pady=(2, 5))
        self.detach_button = ttk.Button(self.values_panel, text="Ver gráfica", style="Quiet.TButton",
                                        command=self.open_preview_window, state="disabled")
        self.detach_button.pack(anchor="w", pady=(0, 4))
        grid = ttk.Frame(self.values_panel, style="Page.TFrame")
        grid.pack(fill="x")
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="months")
        for i, var in enumerate(self.months):
            row, col = divmod(i, 3)
            ttk.Label(grid, text=f"Mes {i+1:02d}", style="Hint.TLabel").grid(
                row=row*2, column=col, sticky="w", padx=3)
            ttk.Entry(grid, textvariable=var, style="Month.Field.TEntry", width=6).grid(
                row=row*2+1, column=col, sticky="ew", padx=3, pady=(0, 2))
        ttk.Button(self.values_panel, text="Copiar para Excel",
                   style="Secondary.TButton", command=self.copy_consumption,
                   cursor="hand2").pack(anchor="w", pady=(5, 0))
        self.build_review_tab()

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(2, weight=1)
        self.export_button = ttk.Button(actions, text="Generar TXT",
                                        style="Primary.TButton", command=self.export, cursor="hand2")
        self.export_button.grid(row=0, column=0, sticky="w")
        self.clear_button = ttk.Button(actions, text="Limpiar todo", style="Quiet.TButton",
                                       command=self.clear_all, cursor="hand2")
        self.clear_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.review_button = ttk.Button(actions, text="Ver avisos", style="Quiet.TButton",
                                         command=self.review_warnings, state="disabled", cursor="hand2")
        self.review_button.grid(row=0, column=2, sticky="e")

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
        self.update_scroll_region()
        self.after_idle(self.fit_current_tab)

    def resize_footer(self, event):
        wrap = max(260, event.width - 55)
        self.status_label.configure(wraplength=wrap)
        self.notice_label.configure(wraplength=wrap)
        if event.height < 435 and self.notice_label.winfo_manager():
            self.notice_label.pack_forget()
        elif event.height >= 435 and not self.notice_label.winfo_manager():
            self.notice_label.pack(anchor="w", pady=(2, 0))

    def change_tab(self, event):
        self.canvas.yview_moveto(0)
        selected = event.widget.select()
        self.resize_for_consumption(selected == str(self.consumption_tab))
        if selected == str(self.consumption_tab):
            self.after_idle(self.schedule_preview)
        if selected == str(self.review_tab):
            self.refresh_review()
        self.after_idle(self.fit_current_tab)

    def resize_for_consumption(self, selected):
        if not selected:
            if self.consumption_window:
                previous, expanded = self.consumption_window
                if self.state() == "normal" and self.geometry() == expanded:
                    self.geometry(previous)
                self.consumption_window = None
            return
        if (self.state() != "normal" or self.consumption_window or
                self.winfo_height() != self.compact_height or
                self.consumption_height <= self.compact_height or
                self.winfo_width() < 720):
            return
        previous = self.geometry()
        match = re.fullmatch(r"(\d+)x\d+([+-]\d+)([+-]\d+)", previous)
        if match:
            width, x, y = match.groups()
            if y.startswith("+"):
                y = f"+{min(int(y), max(0, self.winfo_screenheight()-self.consumption_height-70))}"
            expanded = f"{width}x{self.consumption_height}{x}{y}"
            self.consumption_window = previous, expanded
            self.geometry(expanded)

    def fit_current_tab(self):
        selected = self.notebook.select()
        if selected:
            page = self.nametowidget(selected)
            height = page.winfo_reqheight()
            if page == self.consumption_tab and self.preview_side_by_side and not self.preview_detached:
                # Expand the PDF pane into unused space instead of leaving it below the tabs.
                chrome = self.notebook.winfo_height() - page.winfo_height()
                height = max(height, self.canvas.winfo_height() - chrome - 2)
            if int(self.notebook.cget("height")) != height:
                self.notebook.configure(height=height)

    def resize_consumption_workspace(self, event):
        side_by_side = event.width >= 645
        if side_by_side != self.preview_side_by_side:
            self.layout_consumption(side_by_side)

    def layout_consumption(self, side_by_side):
        self.preview_side_by_side = side_by_side
        if not self.preview_detached:
            self.preview_panel.grid_forget()
        self.values_panel.grid_forget()
        embedded = side_by_side and not self.preview_detached
        self.consumption_workspace.columnconfigure(0, weight=3 if embedded else 1)
        self.consumption_workspace.columnconfigure(1, weight=2 if embedded else 0)
        if embedded:
            self.preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
            self.values_panel.grid(row=0, column=1, sticky="nsew")
        else:
            self.values_panel.grid(row=0, column=0, sticky="nsew")
        self.after_idle(self.fit_current_tab)

    def open_preview_window(self):
        if self.preview_detached:
            self.preview_panel.lift()
            return
        self.preview_panel.grid_forget()
        self.preview_detached = True
        self.wm_manage(self.preview_panel)
        self.tk.call("wm", "title", self.preview_panel, "SolarCAD | Gráfica de consumo")
        self.tk.call("wm", "geometry", self.preview_panel,
                     f"{min(590, self.winfo_screenwidth()-40)}x{min(650, self.winfo_screenheight()-90)}")
        self.tk.call("wm", "minsize", self.preview_panel, 320, 280)
        self.tk.call("wm", "protocol", self.preview_panel, "WM_DELETE_WINDOW",
                     self.preview_close_callback)
        self.layout_consumption(self.preview_side_by_side)
        self.schedule_preview()

    def close_preview_window(self):
        if not self.preview_detached:
            return
        self.wm_forget(self.preview_panel)
        self.preview_detached = False
        self.layout_consumption(self.preview_side_by_side)

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
        self.detach_button.configure(state="normal" if self.preview_total else "disabled")
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
            self.preview_canvas.configure(scrollregion=(0, 0, x+image.width, image.height))
            self.preview_canvas.xview_moveto(old_x)
            self.preview_canvas.yview_moveto(old_y)
        except Exception as exc:
            self.show_preview_placeholder("No se pudo mostrar esta página: " + str(exc))

    def clear_preview(self):
        self.close_preview_window()
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
        if not event.delta:
            return
        direction = -1 if event.delta > 0 else 1
        px, py = self.preview_canvas.winfo_rootx(), self.preview_canvas.winfo_rooty()
        if (self.preview_canvas.winfo_ismapped() and
            px <= event.x_root < px+self.preview_canvas.winfo_width() and
                py <= event.y_root < py+self.preview_canvas.winfo_height()):
            steps = 3 * max(1, abs(event.delta) // 120)
            if event.state & 0x0001:
                self.scroll_view(self.preview_canvas, "x", direction * steps)
            else:
                self.scroll_view(self.preview_canvas, "y", direction * steps)
            return "break"
        if event.widget.winfo_toplevel() != self:
            return
        x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        if x <= event.x_root < x+self.canvas.winfo_width() and y <= event.y_root < y+self.canvas.winfo_height():
            self.scroll_view(self.canvas, "y", direction)
            return "break"

    @staticmethod
    def scroll_view(canvas, axis, steps):
        view = canvas.xview if axis == "x" else canvas.yview
        start, end = view()
        if (steps < 0 and start > 0.0) or (steps > 0 and end < 1.0):
            view("scroll", steps, "units")

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
        style.configure("Compact.Field.TEntry", padding=(7, 2))
        style.configure("Month.Field.TEntry", padding=(7, 1))
        style.configure("Main.TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("Main.TNotebook.Tab", background="#E5EBF2", foreground=MUTED,
                        padding=(12, 6), font=("Segoe UI", 10, "bold"))
        style.map("Main.TNotebook.Tab", background=[("selected", NAVY), ("active", "#D2E1ED")],
                  foreground=[("selected", "white")])
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
        self.header_logo = ImageTk.PhotoImage(logo.resize((38, 38), Image.Resampling.LANCZOS))
        tk.Label(banner, image=self.header_logo, bg=NAVY).pack(side="left")

    def form_card(self, parent, row, title, accent):
        card = ttk.Frame(parent, style="Page.TFrame")
        parent.columnconfigure(0, weight=1)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8) if row == 0 else 0)
        tk.Frame(card, bg=accent, height=4).pack(fill="x")
        content = ttk.Frame(card, style="Page.TFrame", padding=(10, 7))
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=title, style="Section.TLabel").pack(anchor="w")
        return content

    def field_group(self, parent, rows, *, tab):
        group = ttk.Frame(parent, style="Page.TFrame")
        group.pack(fill="x", pady=(7, 0))
        columns = max(len(keys) for keys in rows)
        for col in range(columns):
            group.columnconfigure(col, weight=1, uniform="fields")
        for row, keys in enumerate(rows):
            for col, key in enumerate(keys):
                cell = ttk.Frame(group, style="Page.TFrame")
                cell.grid(row=row, column=col, columnspan=columns if len(keys) == 1 else 1,
                          sticky="ew", padx=(0, 8) if col < len(keys)-1 else 0, pady=(0, 2))
                ttk.Label(cell, text=LABELS[key], style="Hint.TLabel").pack(anchor="w")
                entry = ttk.Entry(cell, textvariable=self.vars[key], style="Compact.Field.TEntry", width=8)
                entry.pack(fill="x")
                self.field_entries[key] = (tab, entry)

    def build_review_tab(self):
        self.review_tab = ttk.Frame(self.notebook, style="Page.TFrame", padding=(16, 10))
        self.notebook.add(self.review_tab, text="Revisión", underline=2)
        ttk.Label(self.review_tab, text="Antes de generar el TXT", style="Section.TLabel").pack(anchor="w")
        self.review_summary = ttk.Label(self.review_tab, style="Hint.TLabel")
        self.review_summary.pack(anchor="w", pady=(2, 5))
        table = ttk.Frame(self.review_tab, style="Page.TFrame")
        table.pack(fill="x")
        self.review_values = {}
        for col, keys in enumerate((("NOMBRE", "DIRECCION", "PUEBLO", "PREPA_CASE",
                                     "PREPA_METER", "LOCATION_ID"),
                                    ("SYSTEM_ID", "GPS_LAT", "GPS_LON", "LAMBERT_X", "LAMBERT_Y"))):
            table.columnconfigure(col, weight=1, uniform="review")
            section = ttk.Frame(table, style="Page.TFrame")
            section.grid(row=0, column=col, sticky="new", padx=(0, 10) if col == 0 else 0)
            section.columnconfigure(0, weight=1)
            for row, key in enumerate(keys):
                ttk.Label(section, text=LABELS[key], style="Hint.TLabel").grid(
                    row=row*2, column=0, sticky="w")
                value = ttk.Label(section, style="Page.TLabel", width=1)
                value.grid(row=row*2+1, column=0, sticky="ew")
                self.review_values[key] = value
                ttk.Button(section, text="Editar", style="Quiet.TButton", padding=(6, 2),
                           command=lambda field=key: self.edit_review_field(field)).grid(
                    row=row*2, column=1, rowspan=2, padx=(5, 0), pady=2)
        self.refresh_review()

    def refresh_review(self):
        missing = 0
        for key, label in self.review_values.items():
            value = self.vars[key].get().strip()
            missing += not value
            label.configure(text=value or "Sin valor", foreground=NAVY if value else MUTED)
        self.review_summary.configure(text=f"{len(KEYS)-missing} de {len(KEYS)} campos completos · Se omiten los vacíos.")

    def edit_review_field(self, key):
        page, entry = self.field_entries[key]
        self.notebook.select(page)
        def focus_field():
            entry.focus_set()
            # Mostrar el campo también cuando queda debajo del borde inferior.
            top = entry.winfo_rooty() - self.canvas.winfo_rooty()
            if top + entry.winfo_height() > self.canvas.winfo_height():
                height = self.canvas.bbox(self.content_window)[3] - self.canvas.bbox(self.content_window)[1]
                self.canvas.yview_moveto(max(0, top - 36) / max(1, height))
        self.after_idle(focus_field)

    def review_warnings(self):
        if self.warnings:
            messagebox.showwarning("Avisos de la factura", "\n".join(self.warnings))

    def set_feedback(self, message, kind="normal"):
        self.status.set(message)
        self.status_label.configure(style={"success": "Success.TLabel",
                                           "error": "Error.TLabel"}.get(kind, "Status.TLabel"))

    def convert_coordinates(self):
        source = self.google_coords.get()
        try:
            latitude, longitude, x, y = convert_google_coordinates(source)
        except (ValueError, RuntimeError) as exc:
            self.coordinate_hint.set(str(exc))
            self.coordinate_hint_label.configure(style="CoordinatesError.TLabel")
            self.set_feedback("No se convirtieron las coordenadas. Corrige la entrada.", "error")
            return
        self.google_coords.set(unwrap_coordinates(source))
        for key, value in zip(("GPS_LAT", "GPS_LON", "LAMBERT_X", "LAMBERT_Y"),
                              (latitude, longitude, x, y)):
            self.vars[key].set(value)
        self.coordinate_hint.set("Conversión completada.")
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
        self.refresh_review()

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
        self.refresh_review()

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
