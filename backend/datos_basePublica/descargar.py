"""
descargar.py — baja la muestra 1 del CMS DE-SynPUF y la deja lista para preparar.py

Uso:
    python descargar.py              # verifica que los enlaces respondan
    python descargar.py --descargar  # descarga y descompone en extraidos/

Los tres archivos juntos pesan 40 MB comprimidos y 193 MB descomprimidos.

Nota: algunas redes corporativas y entornos con lista blanca de dominios
bloquean cms.gov. Si la verificación falla con 403 pero el navegador sí abre
la página, descarga los tres .zip a mano desde la página de la muestra 1 y
deja los .csv en extraidos/ con sus nombres originales.

Fuente: Centers for Medicare & Medicaid Services, dominio público.
Los datos son sintéticos: sirven para construir herramientas, no para inferir
sobre la población real de Medicare.
"""

import argparse
import sys
import zipfile
from pathlib import Path

import requests

PAGINA = (
    "https://www.cms.gov/data-research/statistics-trends-and-reports/"
    "medicare-claims-synthetic-public-use-files/"
    "cms-2008-2010-data-entrepreneurs-synthetic-public-use-file-de-synpuf/de10-sample-1"
)

BASE = (
    "https://www.cms.gov/research-statistics-data-and-systems/"
    "downloadable-public-use-files/synpufs/downloads/"
)

# tabla -> (archivo zip, csv que contiene, MB comprimido esperado)
ARCHIVOS = {
    "afiliados": (
        "de1_0_2008_beneficiary_summary_file_sample_1.zip",
        "DE1_0_2008_Beneficiary_Summary_File_Sample_1.csv",
        3.0,
    ),
    "hospitalario": (
        "de1_0_2008_to_2010_inpatient_claims_sample_1.zip",
        "DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.csv",
        4.0,
    ),
    "ambulatorio": (
        "de1_0_2008_to_2010_outpatient_claims_sample_1.zip",
        "DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.csv",
        32.9,
    ),
}

CRUDOS = Path("crudos")
EXTRAIDOS = Path("extraidos")


def mb(n):
    return f"{n / 1_048_576:.1f} MB"


def verificar():
    todo_ok = True
    for tabla, (zip_, _, esperado) in ARCHIVOS.items():
        try:
            r = requests.head(BASE + zip_, allow_redirects=True, timeout=60)
            tam = int(r.headers.get("content-length", 0))
            tipo = r.headers.get("content-type", "?")
            ok = r.status_code == 200 and "zip" in tipo.lower()
            marca = "OK " if ok else "MAL"
            print(f"{marca} {tabla:<14} {r.status_code}  {mb(tam):>9}  (esperado ~{esperado} MB)")
            todo_ok &= ok
        except requests.RequestException as e:
            print(f"MAL {tabla:<14} {type(e).__name__}: {e}")
            todo_ok = False
    return todo_ok


def descargar():
    CRUDOS.mkdir(exist_ok=True)
    EXTRAIDOS.mkdir(exist_ok=True)

    for tabla, (zip_, csv_, _) in ARCHIVOS.items():
        destino = CRUDOS / zip_

        if destino.exists() and zipfile.is_zipfile(destino):
            print(f"ya está     {zip_}  ({mb(destino.stat().st_size)})")
        else:
            print(f"bajando     {zip_} ...", end=" ", flush=True)
            with requests.get(BASE + zip_, stream=True, timeout=900) as r:
                r.raise_for_status()
                with open(destino, "wb") as f:
                    for trozo in r.iter_content(chunk_size=1 << 20):
                        f.write(trozo)
            print(mb(destino.stat().st_size))
            if not zipfile.is_zipfile(destino):
                print(f"  ERROR: no es un zip válido. Bórralo y reintenta.")
                continue

        salida = EXTRAIDOS / csv_
        if salida.exists():
            print(f"  ya extraído {csv_}  ({mb(salida.stat().st_size)})")
        else:
            with zipfile.ZipFile(destino) as z:
                z.extract(csv_, EXTRAIDOS)
            print(f"  extraído    {csv_}  ({mb(salida.stat().st_size)})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--descargar", action="store_true")
    args = p.parse_args()

    print("CMS DE-SynPUF, muestra 1\n")
    ok = verificar()

    if not ok:
        print(f"\nAlgún enlace falló. Página oficial:\n  {PAGINA}")
        print("Si el navegador sí la abre, baja los zip a mano y deja los csv en extraidos/.")
        sys.exit(1)

    print("\nLos tres enlaces responden.")
    if args.descargar:
        print()
        descargar()
        print("\nListo. Sigue con:  python preparar.py")
    else:
        print("Corre con --descargar para bajarlos.")
