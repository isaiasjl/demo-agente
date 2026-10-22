"""Prueba de punta a punta: levanta el backend y el front, les habla por HTTP y los apaga.

    uv run python probar_demo.py

Usa el modelo real (Claude en tu máquina, o Foundry si lo tienes configurado),
así que tarda unos minutos y gasta cuota.
"""

import os
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

# si una respuesta trae emojis y la consola es cp1252, el print truena a media prueba
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.abspath(__file__))
P_BACK, P_FRONT, P_FRONT_PROD, P_FRONT_MALA = 8101, 3990, 3992, 3993
BACK = f"http://127.0.0.1:{P_BACK}"
FRONT = f"http://127.0.0.1:{P_FRONT}"
CLAVE = f"clave-de-prueba-{uuid.uuid4().hex[:8]}"

fallos = 0
logs: list[tuple[str, str]] = []


def chequeo(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'ERR'}] {nombre}" + (f"  -> {detalle}" if detalle else ""), flush=True)


def lanzar(nombre: str, modulo: str, env_extra: dict) -> subprocess.Popen:
    log = tempfile.NamedTemporaryFile(prefix=f"demo_{nombre}_", suffix=".log", delete=False)
    logs.append((nombre, log.name))
    entorno = {**os.environ, "PYTHONIOENCODING": "utf-8", **env_extra}
    if "PORT" in env_extra:  # por si un .env define BACKEND_PORT o FRONT_PORT
        entorno["BACKEND_PORT" if modulo.endswith("backend") else "FRONT_PORT"] = env_extra["PORT"]
    return subprocess.Popen([sys.executable, "-m", modulo], cwd=RAIZ, env=entorno, stdout=log, stderr=subprocess.STDOUT)


def esperar(url: str, p: subprocess.Popen, intentos: int = 90) -> bool:
    for _ in range(intentos):
        if p.poll() is not None:
            return False
        try:
            httpx.get(url, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def apagar(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def terminar_solo(p: subprocess.Popen, segundos: float = 25) -> int | None:
    """Espera a que el proceso se cierre solo. Devuelve None si sigue vivo (y lo apaga)."""
    try:
        return p.wait(timeout=segundos)
    except subprocess.TimeoutExpired:
        apagar(p)
        return None


def cola_del_log(nombre: str, lineas: int = 12) -> str:
    for n, ruta in logs:
        if n == nombre:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                return "\n".join(f.read().splitlines()[-lineas:])
    return ""


def actividad(texto: str, conversacion: str, tipo: str = "message", actividad_id: str | None = None, **extra) -> dict:
    return {
        "type": tipo, "id": actividad_id or str(uuid.uuid4()), "channelId": "prueba-local",
        "serviceUrl": "http://localhost:9", "deliveryMode": "expectReplies",
        "from": {"id": "usuario-1", "name": "Usuario"}, "recipient": {"id": "agente-1", "name": "Agente"},
        "conversation": {"id": conversacion}, "locale": "es-MX", "text": texto, **extra,
    }


def front_habla(url: str, texto: str, conversacion: str, **kw) -> list[str]:
    r = httpx.post(f"{url}/api/messages", json=actividad(texto, conversacion, **kw), timeout=180)
    if r.status_code != 200:
        return [f"HTTP {r.status_code}"]
    return [a.get("text", "") for a in r.json().get("activities", []) if a.get("type") == "message"]


def back_chat(texto: str, conversacion: str, clave: str | None = CLAVE) -> httpx.Response:
    cabeceras = {"X-API-Key": clave} if clave else {}
    return httpx.post(f"{BACK}/chat", json={"message": texto, "conversation_id": conversacion}, headers=cabeceras, timeout=180)


def principal() -> int:
    backend = lanzar("backend", "demo_agente.backend", {"PORT": str(P_BACK), "BACKEND_API_KEY": CLAVE})
    front = lanzar("front", "demo_agente.front", {"PORT": str(P_FRONT), "BACKEND_URL": BACK, "BACKEND_API_KEY": CLAVE})
    extras: list[subprocess.Popen] = []
    try:
        print("\n== 1) Arranque ==")
        b_ok = esperar(f"{BACK}/health", backend)
        chequeo("el backend (FastAPI + agente) arranca", b_ok)
        f_ok = esperar(f"{FRONT}/health", front)
        chequeo("el front (Agents SDK) arranca", f_ok)
        if not (b_ok and f_ok):
            for nombre in ("backend", "front"):
                print(f"   --- log {nombre} ---\n{cola_del_log(nombre)}")
            return 1

        print("\n== 2) Backend directo ==")
        chequeo("/health responde sin clave", httpx.get(f"{BACK}/health").status_code == 200)
        chequeo("/chat sin clave -> 401", back_chat("hola", "b0", clave=None).status_code == 401)
        chequeo("/chat con clave incorrecta -> 401", back_chat("hola", "b0", clave="mala").status_code == 401)
        chequeo("mensaje vacío -> 422", back_chat("   ", "b0").status_code == 422)
        chequeo("mensaje demasiado largo -> 422", back_chat("x" * 4001, "b0").status_code == 422)
        conv = f"b-{uuid.uuid4().hex[:6]}"
        r = back_chat("Me llamo Alicia.", conv)
        chequeo("/chat con clave correcta -> 200 y respuesta", r.status_code == 200 and bool(r.json().get("reply")), repr(r.text[:80]))
        r = back_chat("¿Cómo me llamo?", conv)
        chequeo("recuerda la conversación", r.status_code == 200 and "Alicia" in r.json()["reply"], repr(r.json().get("reply", "")[:70]))
        r = back_chat("¿Cómo me llamo?", f"b-{uuid.uuid4().hex[:6]}")
        chequeo("otra conversación no ve la de Alicia", r.status_code == 200 and "Alicia" not in r.json()["reply"], repr(r.json().get("reply", "")[:70]))
        r = back_chat("¿A cuánto está el dólar?", conv)
        chequeo("usa la herramienta de tipo de cambio (18.45)", r.status_code == 200 and ("18.45" in r.json()["reply"] or "18,45" in r.json()["reply"]), repr(r.json().get("reply", "")[:90]))
        r = back_chat("Calcula el interés compuesto de $10,000 al 12% anual por 12 meses.", conv)
        texto = r.json().get("reply", "") if r.status_code == 200 else r.text
        chequeo("usa la herramienta de interés compuesto (11,268.25)", "11,268.25" in texto or "11268.25" in texto, repr(texto[:110]))
        r = httpx.post(f"{BACK}/reset", json={"conversation_id": conv}, headers={"X-API-Key": CLAVE})
        chequeo("/reset borra la memoria", r.status_code == 200 and r.json().get("existia") is True, r.text)
        r = back_chat("¿Cómo me llamo?", conv)
        chequeo("después del reset ya no sabe el nombre", r.status_code == 200 and "Alicia" not in r.json()["reply"], repr(r.json().get("reply", "")[:70]))

        print("\n== 3) Front (modo local sin token, como lo llamaría Teams) ==")
        chequeo("/health del front responde", httpx.get(f"{FRONT}/health").status_code == 200)
        t = front_habla(FRONT, "", "f0", tipo="conversationUpdate", membersAdded=[{"id": "usuario-1", "name": "Usuario"}])
        chequeo("conversationUpdate -> mensaje de bienvenida", any("demo de mIA" in x for x in t), repr(t)[:90])
        conv_f = f"f-{uuid.uuid4().hex[:6]}"
        t = front_habla(FRONT, "Me llamo Beto.", conv_f)
        chequeo("mensaje -> respuesta del agente a través del backend", bool(t) and not t[0].startswith("HTTP"), repr(t)[:90])
        t = front_habla(FRONT, "¿Cómo me llamo?", conv_f)
        chequeo("la memoria se conserva entre turnos", bool(t) and "Beto" in t[0], repr(t)[:90])
        t = front_habla(FRONT, "¿Cómo me llamo?", f"f-{uuid.uuid4().hex[:6]}")
        chequeo("otra conversación de Teams no hereda la de Beto", bool(t) and "Beto" not in t[0], repr(t)[:90])
        t = front_habla(FRONT, "¿A cuánto está el dólar?", conv_f)
        chequeo("el agente usa su herramienta a través de Teams (18.45)", bool(t) and ("18.45" in t[0] or "18,45" in t[0]), repr(t)[:100])
        t = front_habla(FRONT, "reset", conv_f)
        chequeo("el comando reset contesta que empezó de nuevo", bool(t) and "nueva" in t[0].lower(), repr(t)[:80])
        t = front_habla(FRONT, "¿Cómo me llamo?", conv_f)
        chequeo("después de reset empieza sin memoria", bool(t) and "Beto" not in t[0], repr(t)[:80])
        fijo = str(uuid.uuid4())
        t1 = front_habla(FRONT, "Me llamo Dana.", f"f-{uuid.uuid4().hex[:6]}", actividad_id=fijo)
        t2 = front_habla(FRONT, "Me llamo Dana.", f"f-{uuid.uuid4().hex[:6]}", actividad_id=fijo)
        chequeo("una Activity repetida (mismo id) se ignora", bool(t1) and t2 == [], f"1a={t1!r:.40} 2a={t2!r:.40}")
        t = front_habla(FRONT, "<at>Asistente</at> Me llamo Carla.", f"f-{uuid.uuid4().hex[:6]}")
        chequeo("quita la @mención que Teams antepone en canales y grupos", bool(t) and "Carla" in t[0], repr(t)[:80])
        r = httpx.post(f"{FRONT}/api/messages", content="esto no es json", headers={"Content-Type": "text/plain"}, timeout=10)
        chequeo("una petición que no es JSON se rechaza con 400", r.status_code == 400, f"HTTP {r.status_code}")

        print("\n== 4) Seguridad ==")
        prod = lanzar("front_prod", "demo_agente.front", {
            "PORT": str(P_FRONT_PROD), "BACKEND_URL": BACK, "BACKEND_API_KEY": CLAVE,
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": str(uuid.uuid4()),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "valor-falso-solo-para-arrancar",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": str(uuid.uuid4()),
        })
        extras.append(prod)
        if esperar(f"http://127.0.0.1:{P_FRONT_PROD}/health", prod):
            chequeo("modo producción: /health no pide token", httpx.get(f"http://127.0.0.1:{P_FRONT_PROD}/health").status_code == 200)
            r = httpx.post(f"http://127.0.0.1:{P_FRONT_PROD}/api/messages", json=actividad("hola", "sin-token"), timeout=10)
            chequeo("modo producción: /api/messages sin token de Entra ID -> 401", r.status_code == 401, f"HTTP {r.status_code}")
        else:
            chequeo("el front arranca en modo producción", False)
            print(f"   --- log ---\n{cola_del_log('front_prod')}")
        expuesto = lanzar("front_expuesto", "demo_agente.front", {"PORT": "3994", "HOST": "0.0.0.0"})
        codigo = terminar_solo(expuesto, 20)
        chequeo("el modo sin token se niega a escuchar en 0.0.0.0", codigo not in (None, 0) and "127.0.0.1" in cola_del_log("front_expuesto"), f"código={codigo}")
        back_expuesto = lanzar("backend_expuesto", "demo_agente.backend", {"PORT": "8102", "HOST": "0.0.0.0", "BACKEND_API_KEY": ""})
        codigo = terminar_solo(back_expuesto, 20)
        chequeo("el backend sin clave se niega a escuchar en 0.0.0.0", codigo not in (None, 0) and "BACKEND_API_KEY" in cola_del_log("backend_expuesto"), f"código={codigo}")
        sin_clave = lanzar("backend_sin_clave", "demo_agente.backend", {"PORT": "8103", "REQUIRE_API_KEY": "true", "BACKEND_API_KEY": ""})
        codigo = terminar_solo(sin_clave, 25)
        chequeo("REQUIRE_API_KEY=true sin BACKEND_API_KEY impide arrancar", codigo not in (None, 0), f"código={codigo}")

        print("\n== 5) Cuando algo falla ==")
        mala = lanzar("front_clave_mala", "demo_agente.front", {"PORT": str(P_FRONT_MALA), "BACKEND_URL": BACK, "BACKEND_API_KEY": "otra-clave"})
        extras.append(mala)
        if esperar(f"http://127.0.0.1:{P_FRONT_MALA}/health", mala):
            t = front_habla(f"http://127.0.0.1:{P_FRONT_MALA}", "hola", "mala-1")
            chequeo("con la clave del backend mal puesta, el usuario recibe un mensaje genérico",
                    bool(t) and "no está disponible" in t[0] and "401" not in t[0] and "clave" not in t[0].lower(), repr(t)[:90])
        apagar(backend)
        t = front_habla(FRONT, "hola otra vez", conv_f)
        chequeo("con el backend caído el usuario recibe un mensaje genérico", bool(t) and "no está disponible" in t[0], repr(t)[:90])
        chequeo("... que no filtra detalles internos",
                bool(t) and not any(x in t[0].lower() for x in ("8101", "127.0.0.1", "connect", "httpx", "traceback")))
        chequeo("... y el front sigue vivo", httpx.get(f"{FRONT}/health").status_code == 200)
    finally:
        for p in (front, backend, *extras):
            apagar(p)
    print("\n  (procesos apagados)")
    for _, ruta in logs:
        try:
            os.unlink(ruta)
        except OSError:
            pass
    print(f"\nResultado: {'TODO OK' if not fallos else str(fallos) + ' comprobación(es) con ERR'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(principal())
