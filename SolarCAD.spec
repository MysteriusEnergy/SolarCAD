# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

logo = str(Path(SPECPATH) / "img" / "a_clean_vector_style_logo_on_a_transparent_backgro.png")
grid = str(Path(SPECPATH) / "grids" / "us_noaa_pvhpgn.tif")
datas, binaries, hiddenimports = collect_all("pymupdf")
proj_datas, proj_binaries, proj_hiddenimports = collect_all("pyproj")
datas += proj_datas
binaries += proj_binaries
hiddenimports += proj_hiddenimports
datas.append((logo, "img"))
datas.append((grid, "grids"))

a = Analysis(
    ["SolarCAD_V3.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SolarCAD",
    console=False,
    icon=logo,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SolarCAD",
)
