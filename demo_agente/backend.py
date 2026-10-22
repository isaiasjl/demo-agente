"""Backend de la demo: el agente de MAF expuesto con FastAPI.

POST /chat, POST /reset y GET /health. Las sesiones van en memoria, una por conversation_id,
así que solo sirve con una réplica.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .agente import NOMBRE, crear_agente, herramientas_usadas, proveedor

load_dotenv()

HOST = os.environ.get("HOST", "127.0.0.1")
PUERTO = int(os.environ.get("BACKEND_PORT") or os.environ.get("PORT") or "8088")
TIEMPO_AGENTE = float(os.environ.get("AGENT_TIMEOUT", "90"))
MAX_SESIONES = int(os.environ.get("MAX_SESIONES", "500"))
LOOPBACK = ("127.0.0.1", "localhost", "::1")

logger = logging.getLogger("backend")


def _huella(conversacion: str) -> str:
    # para el log, sin escribir el id completo
    return hashlib.sha256(conversacion.encode()).hexdigest()[:8]


def _quiere_clave() -> bool:
    return os.environ.get("REQUIRE_API_KEY", "").strip().lower() in ("1", "true", "yes", "si")


class ChatEntrada(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Lo que escribió el usuario.")
    conversation_id: str | None = Field(default=None, max_length=200, description="Id de la conversación. Si falta, se crea uno.")


class ChatSalida(BaseModel):
    reply: str
    conversation_id: str
    tools_used: list[str] = []
    ms: int


class ResetEntrada(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)


def verificar_clave(x_api_key: str | None = Header(default=None)) -> None:
    esperada = os.environ.get("BACKEND_API_KEY", "")
    if not esperada:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), esperada.encode()):
        raise HTTPException(status_code=401, detail="Clave inválida o ausente.")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    if _quiere_clave() and not os.environ.get("BACKEND_API_KEY"):
        raise RuntimeError("REQUIRE_API_KEY está activo pero falta BACKEND_API_KEY.")
    async with AsyncExitStack() as pila:
        agente = crear_agente()
        if hasattr(agente, "__aenter__"):  # ClaudeAgent se usa con async with
            agente = await pila.enter_async_context(agente)
        app.state.agente = agente
        app.state.sesiones = OrderedDict()
        app.state.candados = {}
        yield


_docs = os.environ.get("ENABLE_DOCS", "true").strip().lower() not in ("0", "false", "no")
app = FastAPI(
    title="Backend demo (MAF)",
    lifespan=ciclo_de_vida,
    docs_url="/docs" if _docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs else None,
)


def _sesion(aplicacion: FastAPI, conversacion: str):
    sesiones: OrderedDict = aplicacion.state.sesiones
    if conversacion in sesiones:
        sesiones.move_to_end(conversacion)
        return sesiones[conversacion]
    sesion = aplicacion.state.agente.create_session()
    sesiones[conversacion] = sesion
    while len(sesiones) > MAX_SESIONES:  # se tira la más vieja
        vieja, _ = sesiones.popitem(last=False)
        aplicacion.state.candados.pop(vieja, None)
    return sesion


@app.get("/health")
async def salud() -> dict:
    return {"status": "ok", "proveedor": proveedor(), "agente": NOMBRE}


@app.post("/chat", response_model=ChatSalida, dependencies=[Depends(verificar_clave)])
async def chat(entrada: ChatEntrada, request: Request) -> ChatSalida:
    mensaje = entrada.message.strip()
    if not mensaje:
        raise HTTPException(status_code=422, detail="El mensaje está vacío.")
    conversacion = entrada.conversation_id or uuid.uuid4().hex
    candado = request.app.state.candados.setdefault(conversacion, asyncio.Lock())  # un turno a la vez por conversación
    inicio = time.perf_counter()
    async with candado:
        sesion = _sesion(request.app, conversacion)
        try:
            respuesta = await asyncio.wait_for(request.app.state.agente.run(mensaje, session=sesion), timeout=TIEMPO_AGENTE)
        except TimeoutError:
            logger.warning("conv=%s el agente tardó más de %ss", _huella(conversacion), TIEMPO_AGENTE)
            raise HTTPException(status_code=504, detail="El agente tardó demasiado.") from None
        except Exception:
            logger.exception("conv=%s error al ejecutar el agente", _huella(conversacion))
            raise HTTPException(status_code=502, detail="El agente no pudo responder.") from None
    usadas = herramientas_usadas(respuesta)
    texto = (respuesta.text or "").strip() or "(sin respuesta)"
    ms = int((time.perf_counter() - inicio) * 1000)
    logger.info("conv=%s entrada=%d car. salida=%d car. herramientas=%s %d ms",
                _huella(conversacion), len(mensaje), len(texto), usadas or "-", ms)
    return ChatSalida(reply=texto, conversation_id=conversacion, tools_used=usadas, ms=ms)


@app.post("/reset", dependencies=[Depends(verificar_clave)])
async def reiniciar(entrada: ResetEntrada, request: Request) -> dict:
    existia = request.app.state.sesiones.pop(entrada.conversation_id, None) is not None
    request.app.state.candados.pop(entrada.conversation_id, None)
    return {"ok": True, "existia": existia}


def main() -> None:
    import uvicorn

    con_clave = bool(os.environ.get("BACKEND_API_KEY"))
    if not con_clave and HOST not in LOOPBACK:
        raise SystemExit(
            f"Sin BACKEND_API_KEY el backend no tiene autenticación y solo puede escuchar en 127.0.0.1 (pediste {HOST}). "
            "Define BACKEND_API_KEY o usa HOST=127.0.0.1."
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(f"Backend (FastAPI + MAF) escuchando en http://{HOST}:{PUERTO}  |  modelo: {proveedor()}  |  "
          f"clave: {'SÍ' if con_clave else 'NO (solo local)'}", flush=True)
    if _docs:
        print(f"Prueba interactiva en http://{HOST}:{PUERTO}/docs . Ctrl+C para salir.", flush=True)
    uvicorn.run(app, host=HOST, port=PUERTO, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
