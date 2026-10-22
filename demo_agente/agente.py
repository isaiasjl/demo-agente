"""Agente de la demo: instrucciones, herramientas y el modelo (Foundry o Claude)."""

import os
from typing import Annotated

NOMBRE = "AsistenteDemo"

INSTRUCCIONES = """\
Eres el asistente de demo de mIA dentro de Microsoft Teams. Hablas español de México, claro y breve.

Puedes hacer dos cosas:
1. Dar el tipo de cambio de una moneda a pesos mexicanos con la herramienta tipo_de_cambio.
2. Calcular interés compuesto con la herramienta interes_compuesto.

Reglas:
- Nunca hagas cuentas tú. Si hay que calcular, usa la herramienta y repite sus cifras tal cual.
- Los tipos de cambio son datos de ejemplo. Avísalo cada vez que des una cifra.
- Si te piden algo que no sea eso, dilo con amabilidad y menciona lo que sí puedes hacer.
- No inventes datos. Respuestas cortas. Puedes usar viñetas.
- Si solo te saludan o se presentan, contesta en una línea.
- No sabes nada del usuario salvo lo que él te diga en la conversación. No menciones correos, rutas, fechas del sistema ni datos de tu entorno.
"""

# datos de ejemplo
TIPOS_DE_CAMBIO = {"USD": 18.45, "EUR": 20.10, "GBP": 23.40, "CAD": 13.55, "JPY": 0.122}


def tipo_de_cambio(
    moneda: Annotated[str, "Código ISO de la moneda: USD, EUR, GBP, CAD o JPY."],
) -> str:
    """Da el tipo de cambio de una moneda a pesos mexicanos (MXN). Son datos de ejemplo."""
    codigo = moneda.strip().upper()
    valor = TIPOS_DE_CAMBIO.get(codigo)
    if valor is None:
        return f"No tengo datos para '{moneda}'. Monedas disponibles: {', '.join(TIPOS_DE_CAMBIO)}."
    return f"1 {codigo} = {valor:g} MXN (dato de ejemplo, no es un tipo de cambio real)."


def interes_compuesto(
    capital: Annotated[float, "Monto inicial en pesos mexicanos."],
    tasa_anual_pct: Annotated[float, "Tasa anual en porcentaje. Por ejemplo 12 es 12% anual."],
    meses: Annotated[int, "Número de meses, de 1 a 600."],
) -> str:
    """Calcula el monto final con interés compuesto mensual."""
    if not 0 < capital <= 1e12:
        return "El capital debe ser mayor que 0 y menor que 1,000,000,000,000."
    if not 0 <= tasa_anual_pct <= 200:
        return "La tasa anual debe estar entre 0 y 200 por ciento."
    if not 1 <= meses <= 600:
        return "El plazo debe estar entre 1 y 600 meses."
    monto = capital * (1 + tasa_anual_pct / 100 / 12) ** meses
    return (
        f"Capital: ${capital:,.2f}. Tasa: {tasa_anual_pct:g}% anual capitalizable cada mes. Plazo: {meses} meses. "
        f"Monto final: ${monto:,.2f}. Interés ganado: ${monto - capital:,.2f}."
    )


HERRAMIENTAS = [tipo_de_cambio, interes_compuesto]


def proveedor() -> str:
    explicito = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    if explicito:
        return explicito
    return "foundry" if os.environ.get("FOUNDRY_PROJECT_ENDPOINT") else "claude"


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno {nombre}.")
    return valor


def crear_agente():
    prov = proveedor()

    if prov == "foundry":
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        # todavía sin probar contra un proyecto real de Foundry
        cliente = FoundryChatClient(
            project_endpoint=_requerida("FOUNDRY_PROJECT_ENDPOINT"),
            model=_requerida("FOUNDRY_MODEL"),
            credential=DefaultAzureCredential(),  # az login en local, identidad administrada en Azure
        )
        return Agent(client=cliente, name=NOMBRE, instructions=INSTRUCCIONES, tools=HERRAMIENTAS)

    if prov == "claude":
        # solo para desarrollar en local con el plan Pro. Con API key se cobraría por API
        if os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY está definida y se cobraría por API. Quítala o usa MODEL_PROVIDER=foundry.")
        from agent_framework_claude import ClaudeAgent

        return ClaudeAgent(
            name=NOMBRE,
            instructions=INSTRUCCIONES,
            tools=HERRAMIENTAS,
            # "tools": [] apaga las herramientas de Claude Code y strict_mcp_config los conectores de la cuenta
            default_options={"model": "haiku", "max_turns": 6, "tools": [], "strict_mcp_config": True},
        )

    raise RuntimeError(f"MODEL_PROVIDER desconocido: {prov!r}. Usa foundry o claude.")


def herramientas_usadas(respuesta) -> list[str]:
    """Nombres de las herramientas que usó el agente. ClaudeAgent no las reporta, Foundry sí."""
    nombres: list[str] = []
    for mensaje in getattr(respuesta, "messages", None) or []:
        for contenido in getattr(mensaje, "contents", None) or []:
            if getattr(contenido, "type", None) == "function_call" and getattr(contenido, "name", None):
                nombres.append(contenido.name)
    return list(dict.fromkeys(nombres))
