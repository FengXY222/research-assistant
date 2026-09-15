# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

datas = [('assets/fonts/NotoSansCJKsc-Regular.otf', 'assets/fonts'), ('assets/translation/opus-mt-en-zh', 'assets/translation/opus-mt-en-zh')]
binaries = []
datas += collect_data_files('rapidocr')
binaries += collect_dynamic_libs('ctranslate2')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['win32crypt', 'rapidocr.main', 'onnxruntime', 'pypdfium2', 'ctranslate2', 'sentencepiece'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'tensorflow', 'tensorrt', 'openvino', 'paddle', 'transformers', 'sklearn', 'skimage', 'panel', 'bokeh', 'plotly', 'altair', 'geopandas', 'fiona', 'osgeo', 'vtk', 'vtkmodules', 'xarray', 'dask', 'pyarrow', 'h5py', 'netCDF4', 'cftime', 'statsmodels', 'sqlalchemy', 'selenium', 'folium', 'pyproj', 'imageio', 'av', 'pygame', 'kaleido', 'narwhals', 'branca', 'fsspec', 'mako', 'patsy', 'PyQt5', 'PyQt6', 'PySide2', 'matplotlib', 'pandas', 'scipy', 'pytest', 'numba', 'IPython', 'sphinx', 'docutils', 'nbformat', 'zmq', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='科研助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='科研助手',
)
