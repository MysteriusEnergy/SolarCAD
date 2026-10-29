"""SolarCAD - factura LUMA a TXT compatible con SOLARIMPORT6. Python 3.10+."""

import re
import shutil
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from PIL import Image, ImageTk

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
        self.geometry("960x840")
        self.minsize(820, 830)
        self.configure(background=BACKGROUND)
        self.vars = {k: tk.StringVar() for k in KEYS}
        self.months = [tk.StringVar() for _ in range(12)]
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
        tk.Label(banner, text="LOCAL  •  PDF → TXT", bg=NAVY, fg=GOLD,
                 font=("Segoe UI", 10, "bold")).pack(side="right", padx=5)

        outer = ttk.Frame(self, style="App.TFrame", padding=(24, 12, 24, 12))
        outer.pack(fill="both", expand=True)

        file_card = ttk.Frame(outer, style="Card.TFrame", padding=(18, 8))
        file_card.pack(fill="x")
        ttk.Button(file_card, text="Abrir factura PDF", style="Accent.TButton",
                   command=self.open_pdf).pack(side="left")
        file_info = ttk.Frame(file_card, style="Card.TFrame")
        file_info.pack(side="left", fill="x", expand=True, padx=(18, 0))
        ttk.Label(file_info, text="ARCHIVO ACTUAL", style="Caption.TLabel").pack(anchor="w")
        self.filename = ttk.Label(file_info, text="Ningún archivo seleccionado",
                                  style="Card.TLabel")
        self.filename.pack(anchor="w")

        notebook = ttk.Notebook(outer, style="Main.TNotebook")
        notebook.pack(fill="both", expand=True, pady=(14, 12))
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

        tab2 = ttk.Frame(notebook, style="Page.TFrame", padding=(22, 18))
        notebook.add(tab2, text="Consumo · Excel")
        ttk.Label(tab2, text="Historial de consumo", style="Section.TLabel").pack(anchor="w")
        ttk.Label(tab2,
                  text="12 valores para pegar en Excel. Se omite el primer mes de la gráfica de 13. "
                       "Si la lectura automática falla, escribe los valores aquí.",
                  style="Hint.TLabel", wraplength=780).pack(anchor="w", pady=(4, 18))
        grid = ttk.Frame(tab2, style="Page.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)
        for i, var in enumerate(self.months):
            row, side = divmod(i, 2)
            col = side * 2
            ttk.Label(grid, text=f"Mes {i+1:02d}", style="Page.TLabel").grid(
                row=row, column=col, sticky="w", padx=(0, 12), pady=7)
            ttk.Entry(grid, textvariable=var, style="Field.TEntry", width=18).grid(
                row=row, column=col+1, sticky="ew", padx=(0, 36) if not side else (0, 0), pady=7)
        ttk.Button(tab2, text="Copiar 12 valores para Excel", style="Secondary.TButton",
                   command=self.copy_consumption).pack(anchor="w", pady=(22, 0))

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.pack(fill="x")
        ttk.Button(actions, text="Generar datos_cliente.txt", style="Primary.TButton",
                   command=self.export).pack(side="left")
        ttk.Button(actions, text="Limpiar todo", style="Quiet.TButton",
                   command=self.clear_all).pack(side="left", padx=12)
        self.review_button = ttk.Button(actions, text="Ver avisos", style="Quiet.TButton",
                                        command=self.review_warnings, state="disabled")
        self.review_button.pack(side="right")

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(13, 0))
        ttk.Label(footer, textvariable=self.status, style="Status.TLabel",
                  wraplength=850).pack(anchor="w")
        ttk.Label(footer, textvariable=self.notice, style="Footer.TLabel",
                  wraplength=850).pack(anchor="w", pady=(2, 0))

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background="white")
        style.configure("Page.TFrame", background="white")
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
        style.configure("Field.TEntry", padding=(9, 3), fieldbackground="white",
                        foreground=NAVY, bordercolor="#C8D4DF", font=("Segoe UI", 10))
        style.map("Field.TEntry", bordercolor=[("focus", TEAL)])
        style.configure("Main.TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("Main.TNotebook.Tab", background="#E5EBF2", foreground=MUTED,
                        padding=(18, 9), font=("Segoe UI", 10, "bold"))
        style.map("Main.TNotebook.Tab", background=[("selected", "white")],
                  foreground=[("selected", NAVY)])
        for name, bg, fg in (("Accent", GOLD, NAVY), ("Primary", NAVY, "white"),
                             ("Secondary", TEAL, "white"), ("Quiet", "#E5EBF2", NAVY)):
            style.configure(name + ".TButton", background=bg, foreground=fg,
                            padding=(15, 9), borderwidth=0,
                            font=("Segoe UI", 10, "bold"))
            style.map(name + ".TButton", background=[("active", bg), ("disabled", "#DDE3E9")],
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

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("Facturas PDF", "*.pdf")])
        if not path:
            return
        try:
            data, cons, warnings = extract_pdf(path)
        except Exception as e:
            messagebox.showerror("Error al abrir PDF", str(e))
            return
        for k in KEYS:
            self.vars[k].set(data[k])
        for i, v in enumerate(self.months):
            v.set(cons[i] if len(cons) == 12 else "")
        self.filename.config(text=Path(path).name)
        count = sum(bool(data[k]) for k in ("PREPA_CASE", "PREPA_METER", "LOCATION_ID",
                                            "NOMBRE", "DIRECCION", "PUEBLO"))
        self.status.set(f"Factura cargada · {count} de 6 datos principales · "
                        f"{sum(bool(v) for v in cons)} de 12 consumos")
        self.warnings = [warning for warning in warnings
                         if not warning.startswith("Paginas LUMA detectadas:")]
        self.notice.set("Revisa los campos y los consumos antes de exportar. "
                        + ("Hay avisos disponibles." if self.warnings else ""))
        self.review_button.configure(state="normal" if self.warnings else "disabled")

    def clear_all(self):
        for var in self.vars.values():
            var.set("")
        for var in self.months:
            var.set("")
        self.filename.config(text="Ningún archivo seleccionado")
        self.status.set("Formulario limpio. Selecciona una factura PDF.")
        self.notice.set("Los datos se procesan en este equipo. Revisa cada campo antes de exportar.")
        self.warnings = []
        self.review_button.configure(state="disabled")

    def export(self):
        data = {k: self.vars[k].get().strip() for k in KEYS}
        if not data["PREPA_CASE"]:
            if not messagebox.askyesno(
                "Sin PREPA CASE", "No hay PREPA CASE. ¿Generar TXT de todos modos?"
            ):
                return
        if any(("\n" in v or "\r" in v or "=" in v) for v in data.values()):
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
            messagebox.showerror("Error al guardar", str(e))
            return
        self.status.set(
            "TXT generado: "
            + path
            + " | En AutoCAD usa SOLARIMPORT6 y selecciona este archivo."
        )
        messagebox.showinfo(
            "TXT listo",
            "Archivo guardado. En AutoCAD ejecuta SOLARIMPORT6 y selecciona el TXT.",
        )

    def copy_consumption(self):
        vals = [v.get().strip() for v in self.months]
        if not all(re.fullmatch(r"\d+", v) for v in vals):
            messagebox.showwarning(
                "Consumo incompleto",
                "Debes revisar/completar los 12 valores numéricos antes de copiarlos.",
            )
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(vals))
        self.update()
        self.status.set(
            "12 consumos copiados verticalmente. Pégalos en Excel con Ctrl+V."
        )


if __name__ == "__main__":
    App().mainloop()
