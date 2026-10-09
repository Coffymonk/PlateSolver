# PlateSolver – licence and third-party notices

**PlateSolver** – Copyright © 2026 Miklos Elmberg

PlateSolver is free software: you can redistribute it and/or modify it under the terms of the GNU General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your
option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License (file `LICENSE`) for more details. SPDX: `GPL-3.0-or-later`.

PlateSolver is built on the work of many others. Everything it contains or uses is listed below with its
licence. In the stand-alone apps, the licence texts of the bundled libraries are in the `licenses` folder inside
the program (`_internal/licenses` on Windows and Linux, inside PlateSolver.app on macOS).

## Software bundled in the stand-alone apps

| Component | Used for | Licence | Website |
|---|---|---|---|
| Python | The language PlateSolver is written in, and its runtime | PSF License 2.0 | python.org |
| Qt 6 via PySide6 and Shiboken6 | The user interface | LGPL-3.0 (see `LGPL-3.0.txt`) | qt.io · doc.qt.io/qtforpython |
| NumPy (with OpenBLAS) | Numerical work on images | BSD-3-Clause (OpenBLAS: BSD-3-Clause) | numpy.org |
| Astropy | FITS files, world coordinates (WCS/SIP), cosmology, constellations | BSD-3-Clause | astropy.org |
| astropy-iers-data, pyerfa (ERFA) | Earth-rotation data and astronomical routines used by Astropy | BSD-3-Clause | astropy.org · github.com/liberfa/pyerfa |
| PyYAML, packaging | Used by Astropy | MIT · Apache-2.0 or BSD-2-Clause | pyyaml.org · github.com/pypa/packaging |
| Pillow | JPEG, PNG and TIFF images | MIT-CMU (HPND) | python-pillow.org |
| pillow-heif | iPhone HEIC photos | BSD-3-Clause | github.com/bigcat88/pillow_heif |
| ↳ libheif, libde265 (inside pillow-heif) | HEIF/HEIC decoding | LGPL-3.0 | github.com/strukturag |
| ↳ x265 (inside pillow-heif) | HEVC codec library | GPL-2.0-or-later | x265.org |
| ↳ MinGW-w64 runtime (Windows only) | Runtime for the HEIC libraries | GPL-3.0 with GCC Runtime Library Exception (libgcc, libstdc++); MIT/BSD (libwinpthread) | mingw-w64.org |
| tifffile | TIFF files from astronomy software | BSD-3-Clause | github.com/cgohlke/tifffile |
| imagecodecs | Compressed TIFF/XISF data | BSD-3-Clause (bundled codecs under their own permissive licences, listed in its licence folder) | github.com/cgohlke/imagecodecs |
| lz4, zstandard | Compressed XISF files | BSD-3-Clause | github.com/python-lz4/python-lz4 · github.com/indygreg/python-zstandard |
| openpyxl, et_xmlfile | Reading quiz spreadsheets | MIT | openpyxl.readthedocs.io |
| setuptools | Package metadata at runtime | MIT | github.com/pypa/setuptools |
| OpenSSL | Secure (https) connections | Apache-2.0 | openssl.org |
| libffi | Used by Python | MIT | sourceware.org/libffi |
| Microsoft Visual C++ runtime (Windows only) | Runtime for Python and Qt | Microsoft Visual Studio redistributable licence | microsoft.com |

Qt is used under the LGPL-3.0: the Qt libraries are kept as separate files in the app, so they can be
replaced with other compatible versions. The source code of Qt and of the LGPL libraries above is available
from the projects' websites.

## Tools used to build the apps (not part of PlateSolver)

| Tool | Licence |
|---|---|
| PyInstaller (its start-up program is included in the apps) | GPL-2.0-or-later with the PyInstaller bootloader exception, which allows distributing the result under any licence |
| Inno Setup (Windows installer, if used) | Inno Setup License |
| pytest (automated tests) | MIT |

## Data included in PlateSolver

| Data | Licence and credit |
|---|---|
| OpenNGC catalogue (NGC, IC and Messier objects) | © Mattia Verga, CC BY-SA 4.0 (github.com/mattiaverga/OpenNGC). Changed for PlateSolver: positions converted to degrees, duplicate and non-existent entries removed (`openngc.csv`; the release is recorded in `openngc_version.json`). CC BY-SA 4.0 is compatible with GPL-3.0. |
| Hipparcos stars (built-in star catalogue) | XHIP: Anderson, E. & Francis, C. 2012, Astronomy Letters 38, 331 (CDS VizieR V/137D), based on ESA's Hipparcos mission (ESA 1997); taken from d3-celestial by Olaf Frohn, BSD-3-Clause (`LICENSE-stars-d3-celestial.txt`). |
| Constellation figures, names and boundaries | d3-celestial by Olaf Frohn, BSD-3-Clause (github.com/ofrohn/d3-celestial; `constellations.LICENSE.txt`). |
| Quiz questions, icons, manual | Part of PlateSolver, GPL-3.0-or-later. |

## Data and services used while PlateSolver runs (not included)

| Source | Credit |
|---|---|
| SIMBAD | This software makes use of the SIMBAD database, operated at CDS, Strasbourg, France (Wenger et al. 2000, A&AS 143, 9). |
| VizieR / Tycho-2 (optional download) | VizieR catalogue access tool, CDS, Strasbourg (Ochsenbein et al. 2000, A&AS 143, 23). Tycho-2: Høg, E. et al. 2000, A&A 355, L27. |
| HyperLeda (optional, from an ASTAP installation) | Makarov, D. et al. 2014, A&A 570, A13 (atlas.obs-hp.fr/hyperleda), in the format distributed with ASTAP. |
| astrometry.net (optional online solver) | Lang, D. et al. 2010, AJ 139, 1782 (nova.astrometry.net). |
| Wikipedia | Articles are opened in the web browser; Wikipedia content is © its authors, CC BY-SA. PlateSolver uses the MediaWiki API to find them. |
| ASTAP and its star databases | ASTAP by Han Kleijn (hnsky.org), separate software installed by the user with its own installer, under its own licence (Mozilla Public License 2.0 according to Linux software listings). PlateSolver only runs it as a separate program. |
| XISF file format | Specification by Pleiades Astrophoto, the makers of PixInsight (pixinsight.com/xisf). |
