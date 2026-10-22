# Demo: agente de MAF publicado en Teams

Un agente de **Microsoft Agent Framework (MAF)** detrás de una API con **FastAPI**, y un front hecho con el **Microsoft 365 Agents SDK** que lo conecta con Teams. Es la forma que tendrán los agentes de mIA, en chiquito.

> El nombre correcto del SDK es **Microsoft 365 Agents SDK**. "Agent 365" es otro producto (el registro y control de agentes que menciona el SOW), no el SDK.

## Cómo encaja

```
Teams ──► Azure Bot Service ──► FRONT (front.py)  ──HTTP──►  BACKEND (backend.py)  ──► modelo
          (lo pone Azure)       Agents SDK                    FastAPI + MAF             Foundry en Azure,
                                POST /api/messages            POST /chat                Claude Pro en tu PC
```

| Archivo | Qué hace |
|---|---|
| `demo_agente/agente.py` | El agente: instrucciones, 2 herramientas (tipo de cambio de ejemplo e interés compuesto) y la fábrica que elige el modelo |
| `demo_agente/backend.py` | FastAPI: `POST /chat`, `POST /reset`, `GET /health`. Una sesión de MAF por conversación |
| `demo_agente/front.py` | Agents SDK: recibe de Teams, llama al backend y contesta. Trae bienvenida, comando `reset` y descarta duplicados |
| `probar_demo.py` | Prueba de punta a punta: levanta todo y le habla por HTTP |
| `teams/` | Manifest y script que arma el `.zip` para instalar el bot en Teams |
| `Dockerfile` | Una imagen para las dos piezas (`--build-arg APP=backend` o `APP=front`) |


## Correrlo en tu máquina (sin Azure)

Necesitas `uv` y tu sesión de Claude Code (plan Pro).

```powershell
uv sync
uv run python -m demo_agente.backend     # terminal 1: http://127.0.0.1:8088
uv run python -m demo_agente.front       # terminal 2: http://localhost:3978/api/messages
```

Cada uno imprime una línea de arranque y luego espera. Dos formas de probarlo:

- **El backend solo, en el navegador.** Abre http://127.0.0.1:8088/docs, entra a `POST /chat`, "Try it out" y manda `{"message": "¿A cuánto está el dólar?"}`.
- **El front como lo vería Teams, sin Teams.** Con el **Agents Playground** (el ejemplo oficial del SDK lo usa): `winget install agentsplayground` y luego `agentsplayground -e http://localhost:3978/api/messages`.

Si un puerto está ocupado (por ejemplo porque los ejercicios 16 o 17 siguen corriendo en otra terminal), ciérralos o cambia `BACKEND_PORT` y `FRONT_PORT`. Si cambias el del backend, pon el mismo en `BACKEND_URL` del front.

## Probar todo

```powershell
uv run python probar_demo.py
```

Levanta el backend y el front, les habla por HTTP y los apaga. Usa el modelo real, así que tarda unos minutos y gasta cuota de tu plan.

## Guion para enseñarlo

1. Al abrir el chat sale la bienvenida.
2. "¿A cuánto está el dólar?" usa la herramienta de tipo de cambio y avisa que es un dato de ejemplo.
3. "Calcula el interés de $50,000 al 10% anual por 18 meses" lo calcula una herramienta en código, no el modelo. Es el mismo principio que pide mIA para las reglas exactas.
4. "Me llamo Ana" y luego "¿Cómo me llamo?" muestran la memoria por conversación.
5. "reset" empieza una conversación nueva.
6. Pregúntale algo fuera de su alcance, como el clima. Debe decir que no puede y qué sí hace.

Mientras tanto, la terminal del backend va mostrando cada llamada (conversación, largo del mensaje, herramientas y milisegundos) sin imprimir el contenido, y la del front las Activities que llegan.

## Qué modelo usa

| `MODEL_PROVIDER` | Modelo | Para qué |
|---|---|---|
| `claude` | Claude con tu plan Pro (ClaudeAgent) | Solo desarrollar en tu máquina. No corre en Azure |
| `foundry` | Microsoft Foundry (`FoundryChatClient`) | Azure. Pide `FOUNDRY_PROJECT_ENDPOINT` y `FOUNDRY_MODEL`. Credencial: `az login` en local, identidad administrada en Azure |

Sin `MODEL_PROVIDER` se usa `foundry` si hay `FOUNDRY_PROJECT_ENDPOINT` y `claude` si no.

## Variables

Se pueden poner en un `.env` (copia `.env.example`).

| Variable | Pieza | Qué hace | Por defecto |
|---|---|---|---|
| `MODEL_PROVIDER` | backend | `claude` o `foundry` | `foundry` si hay endpoint, si no `claude` |
| `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` | backend | El proyecto de Foundry y el nombre del despliegue | vacío |
| `BACKEND_PORT` o `PORT` | backend | Puerto | 8088 |
| `FRONT_PORT` o `PORT` | front | Puerto | 3978 |
| `HOST` | ambos | Interfaz donde escuchan. En Azure va `0.0.0.0` | backend `127.0.0.1`, front `localhost` |
| `BACKEND_API_KEY` | ambos | Clave compartida (cabecera `X-API-Key`). Debe ser la misma en los dos | vacía, solo local |
| `REQUIRE_API_KEY` | backend | Si es `true`, no arranca sin clave | `false` |
| `AGENT_TIMEOUT` | backend | Segundos máximos por respuesta del agente | 90 |
| `MAX_SESIONES` | backend | Conversaciones que guarda en memoria | 500 |
| `ENABLE_DOCS` | backend | `/docs` de Swagger | `true` |
| `BACKEND_URL` | front | Dónde está el backend | `http://127.0.0.1:8088` |
| `BACKEND_TIMEOUT` | front | Segundos de espera al backend | 100 |
| `MOSTRAR_HERRAMIENTAS` | front | Añade "Herramientas usadas" a la respuesta, si el modelo lo reporta | `true` |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`, `...CLIENTSECRET`, `...TENANTID` | front | Credenciales del Azure Bot. Con CLIENTID el front **exige** el token de Entra ID | vacío |

## Seguridad

- El front sin credenciales arranca en **modo local anónimo** y solo acepta escuchar en loopback. Con `CLIENTID` valida el token de Entra ID en cada petición y rechaza las que no lo traen (401).
- El backend sin `BACKEND_API_KEY` no tiene autenticación y solo acepta escuchar en `127.0.0.1`. Con la clave, `/chat` y `/reset` la exigen.
- Los errores del backend no llegan al usuario de Teams: recibe "El asistente no está disponible por ahora".
- El backend no registra el contenido de los mensajes, solo una huella de la conversación y las longitudes.
- El SDK enmascara el `CLIENTSECRET` en sus logs de arranque (se comprobó con un valor falso).
- Con `MODEL_PROVIDER=claude`, Claude Code mete en el contexto del modelo datos de tu cuenta, como tu correo. Las instrucciones le piden no mencionarlos, pero **no lo uses con otras personas**: es solo para desarrollar.

## Estado de las pruebas

**Verificado en este equipo**

- `probar_demo.py` con el modelo real (Claude Pro): 34 comprobaciones, todas OK. Incluye que el agente usa sus dos herramientas, tanto llamando al backend directo como a través del front.
- Que la lectura de las llamadas a herramientas (`tools_used`) funciona con los tipos de contenido de MAF. Se probó con mensajes sintéticos, no con un modelo de Foundry.
- El script `teams/empaquetar.py` genera un `.zip` con manifest e iconos del tamaño correcto, y rechaza datos inválidos.

**No probado**

- La rama de Foundry. Solo se comprobó que el cliente se construye: aquí no hay endpoint ni credenciales.
- Azure Bot y Teams. Aquí no hay tenant, así que no se subió el `.zip` ni se habló con el bot desde Teams. El front se probó con Activities simuladas por HTTP.
- El Agents Playground, el Dockerfile y los comandos de `DESPLIEGUE.md`. No hay Node, Docker ni `az` instalados.
- Que `tools_used` salga con Foundry. Con ClaudeAgent la respuesta no trae las llamadas a herramientas, así que el front no muestra "Herramientas usadas". La función que las lee está lista para cuando el modelo las reporte.

## Límites de la demo

Sesiones en memoria (una réplica), sin streaming, sin mensajes proactivos, sin SSO ni permisos por usuario, y una clave compartida entre front y backend. Qué cambia en el mIA real y cómo, en `DESPLIEGUE.md`.
