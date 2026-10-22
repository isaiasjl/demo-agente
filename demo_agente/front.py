"""Front de la demo: recibe los mensajes de Teams con el Microsoft 365 Agents SDK y se los pasa al backend.

Sin CLIENTID corre en modo local, sin token, y solo escucha en loopback.
Con CLIENTID exige el token de Entra ID en cada petición.
"""

import logging
import os
import re
from collections import OrderedDict

import httpx
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# localhost y no 127.0.0.1 para que también escuche en IPv6 (el Agents Playground resuelve localhost a ::1)
HOST = os.environ.get("HOST", "localhost")
PUERTO = int(os.environ.get("FRONT_PORT") or os.environ.get("PORT") or "3978")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8088").rstrip("/")
BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")
BACKEND_TIMEOUT = float(os.environ.get("BACKEND_TIMEOUT", "100"))
MOSTRAR_HERRAMIENTAS = os.environ.get("MOSTRAR_HERRAMIENTAS", "true").strip().lower() not in ("0", "false", "no")
MAX_TEXTO = 20000  # Teams acepta unos 28 KB por mensaje
LOOPBACK = ("127.0.0.1", "localhost", "::1")
VAR_CLIENTID = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"
VAR_ANONIMO = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED"

logger = logging.getLogger("front")

BIENVENIDA = (
    "¡Hola! Soy el asistente de demo de mIA.\n\n"
    "Puedo darte tipos de cambio (datos de ejemplo) y calcular interés compuesto. Prueba con:\n"
    "- ¿A cuánto está el dólar?\n"
    "- Calcula el interés de $50,000 al 10% anual por 18 meses\n\n"
    "Escribe **reset** para empezar una conversación nueva."
)
ERROR_BACKEND = "El asistente no está disponible por ahora. Intenta de nuevo en unos minutos."
PATRON_RESET = re.compile(r"^\s*/?(reset|reiniciar|nueva conversaci[oó]n)\s*[.!]?\s*$", re.IGNORECASE)
PATRON_MENCION = re.compile(r"<at>.*?</at>", re.IGNORECASE)  # en canales y grupos Teams antepone <at>Bot</at>


def _modo_anonimo() -> bool:
    # sin CLIENTID es modo local sin token, con CLIENTID se exige token
    os.environ.setdefault(VAR_ANONIMO, "false" if os.environ.get(VAR_CLIENTID) else "true")
    return os.environ[VAR_ANONIMO].strip().lower() == "true"


def crear_app():
    from microsoft_agents.activity import load_configuration_from_env
    from microsoft_agents.authentication.msal import MsalConnectionManager
    from microsoft_agents.hosting.aiohttp import CloudAdapter
    from microsoft_agents.hosting.core import AgentApplication, Authorization, MemoryStorage, TurnContext, TurnState

    _modo_anonimo()
    config = load_configuration_from_env(os.environ)
    almacenamiento = MemoryStorage()  # en producción usar Blob o Cosmos
    conexiones = MsalConnectionManager(**config)
    adaptador = CloudAdapter(connection_manager=conexiones)
    app = AgentApplication[TurnState](
        storage=almacenamiento, adapter=adaptador,
        authorization=Authorization(almacenamiento, conexiones, **config), **config,
    )

    cliente = httpx.AsyncClient(timeout=httpx.Timeout(BACKEND_TIMEOUT, connect=5.0))
    cabeceras = {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}
    vistos: OrderedDict[str, None] = OrderedDict()  # ids de Activity ya atendidas, Teams reintenta si el servidor tarda

    def _repetida(actividad_id: str | None) -> bool:
        if not actividad_id:
            return False
        if actividad_id in vistos:
            return True
        vistos[actividad_id] = None
        while len(vistos) > 500:
            vistos.popitem(last=False)
        return False

    @app.conversation_update("membersAdded")
    async def al_unirse(context: TurnContext, _state: TurnState):
        for miembro in context.activity.members_added or []:
            if miembro.id != context.activity.recipient.id:
                await context.send_activity(BIENVENIDA)

    @app.message(PATRON_RESET)
    async def al_reiniciar(context: TurnContext, state: TurnState):
        # con otra versión el conversation_id cambia y el backend empieza una sesión nueva
        version = state.get_value("ConversationState.version", lambda: 0)
        state.set_value("ConversationState.version", int(version) + 1)
        await context.send_activity("Listo, empecé una conversación nueva.")

    @app.message()
    async def al_mensaje(context: TurnContext, state: TurnState):
        if _repetida(context.activity.id):
            logger.warning("Activity repetida, se ignora")
            return
        texto = PATRON_MENCION.sub("", context.activity.text or "").strip()
        if not texto:
            return
        version = state.get_value("ConversationState.version", lambda: 0)
        cuerpo = {"message": texto, "conversation_id": f"{context.activity.conversation.id}#{version}"}
        try:
            r = await cliente.post(f"{BACKEND_URL}/chat", json=cuerpo, headers=cabeceras)
            r.raise_for_status()
            datos = r.json()
            respuesta = (datos.get("reply") or "").strip() or "(sin respuesta)"
            if MOSTRAR_HERRAMIENTAS and datos.get("tools_used"):
                respuesta += f"\n\n_Herramientas usadas: {', '.join(datos['tools_used'])}_"
            await context.send_activity(respuesta[:MAX_TEXTO])
        except Exception as exc:
            # el detalle va al log, el usuario solo ve un mensaje genérico
            logger.warning("Falló la llamada al backend: %s: %s", type(exc).__name__, exc)
            await context.send_activity(ERROR_BACKEND)

    @app.error
    async def al_error(context: TurnContext, error: Exception):
        logger.error("Error no controlado: %s", error)
        await context.send_activity("Ocurrió un error. Intenta de nuevo.")

    return app, conexiones, adaptador, cliente


def servir() -> None:
    from microsoft_agents.hosting.aiohttp import jwt_authorization_middleware, start_agent_process

    anonimo = _modo_anonimo()
    if anonimo and HOST not in LOOPBACK:
        raise SystemExit(
            f"El modo local sin token solo puede escuchar en 127.0.0.1 (pediste {HOST}). "
            f"Define {VAR_CLIENTID} y las demás credenciales del Azure Bot para exponerlo."
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app, conexiones, adaptador, cliente = crear_app()

    async def punto_de_entrada(req: web.Request) -> web.Response:
        return await start_agent_process(req, req.app["agent_app"], req.app["adapter"])

    async def salud(_req: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    @web.middleware
    async def token_salvo_salud(req: web.Request, handler):
        # /health no pide token, todo lo demás pasa por la validación de Entra ID
        if req.method == "GET" and req.path == "/health":
            return await handler(req)
        return await jwt_authorization_middleware(req, handler)

    async def cerrar_cliente(_app: web.Application) -> None:
        await cliente.aclose()

    servidor = web.Application(middlewares=[token_salvo_salud])
    servidor.router.add_post("/api/messages", punto_de_entrada)
    servidor.router.add_get("/health", salud)
    servidor["agent_configuration"] = conexiones.get_default_connection_configuration()
    servidor["agent_app"] = app
    servidor["adapter"] = adaptador
    servidor.on_cleanup.append(cerrar_cliente)

    modo = "LOCAL ANÓNIMO (sin token)" if anonimo else "PRODUCCIÓN (valida el token de Entra ID)"
    print(f"Front (Agents SDK) escuchando en http://{HOST}:{PUERTO}/api/messages  |  modo: {modo}", flush=True)
    print(f"Backend: {BACKEND_URL}  |  clave: {'SÍ' if BACKEND_API_KEY else 'NO'}", flush=True)
    print("Está esperando mensajes, no imprime nada más hasta que llegue uno. Ctrl+C para salir.", flush=True)
    web.run_app(servidor, host=HOST, port=PUERTO, print=None)


if __name__ == "__main__":
    servir()
