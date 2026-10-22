"""Arma teams/demo-teams.zip con el manifest y los iconos.

    uv run python teams/empaquetar.py --bot-id <guid> --dominio <dominio-del-front>

--bot-id es el Application (client) ID de la app de Entra ID del Azure Bot.
--dominio es el dominio público del front, sin https://.
"""

import argparse
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

AQUI = Path(__file__).parent
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _png(ancho: int, alto: int, pixel) -> bytes:
    """PNG RGBA sin librerías. pixel(x, y) devuelve (r, g, b, a)."""
    crudo = bytearray()
    for y in range(alto):
        crudo.append(0)  # filtro "ninguno"
        for x in range(ancho):
            crudo.extend(pixel(x, y))

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0))
        + trozo(b"IDAT", zlib.compress(bytes(crudo), 9))
        + trozo(b"IEND", b"")
    )


def icono_color() -> bytes:
    # 192x192, fondo azul con un anillo y un punto blancos
    def pixel(x: int, y: int):
        d = ((x - 95.5) ** 2 + (y - 95.5) ** 2) ** 0.5
        if 46 <= d <= 62 or d <= 14:
            return (255, 255, 255, 255)
        return (15, 76, 129, 255)
    return _png(192, 192, pixel)


def icono_contorno() -> bytes:
    # 32x32, blanco sobre transparente, como pide Teams
    def pixel(x: int, y: int):
        d = ((x - 15.5) ** 2 + (y - 15.5) ** 2) ** 0.5
        if 9 <= d <= 12 or d <= 3:
            return (255, 255, 255, 255)
        return (0, 0, 0, 0)
    return _png(32, 32, pixel)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arma el paquete de Teams (manifest + iconos).")
    parser.add_argument("--bot-id", required=True, help="GUID de la app de Entra ID del Azure Bot")
    parser.add_argument("--dominio", required=True, help="dominio público del front, sin https://")
    parser.add_argument("--salida", default=str(AQUI / "demo-teams.zip"))
    args = parser.parse_args()

    if not GUID.match(args.bot_id):
        raise SystemExit(f"--bot-id no parece un GUID: {args.bot_id!r}")
    dominio = re.sub(r"^https?://", "", args.dominio.strip()).strip("/")
    if not dominio or "/" in dominio or " " in dominio:
        raise SystemExit(f"--dominio debe ser solo el dominio, sin ruta: {args.dominio!r}")

    texto = (AQUI / "manifest.template.json").read_text(encoding="utf-8")
    texto = texto.replace("{{BOT_ID}}", args.bot_id.lower()).replace("{{DOMINIO}}", dominio)
    manifest = json.loads(texto)
    if re.search(r"\{\{.*?\}\}", texto):
        raise SystemExit("Quedó algún {{marcador}} sin reemplazar en el manifest.")

    with zipfile.ZipFile(args.salida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("color.png", icono_color())
        z.writestr("outline.png", icono_contorno())
    print(f"Listo: {args.salida}")
    print(f"  app id y bot id: {args.bot_id.lower()}")
    print(f"  dominio:         {dominio}")


if __name__ == "__main__":
    main()
