# una imagen para las dos piezas: --build-arg APP=backend o APP=front
# no lo he probado, aquí no tengo Docker
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1

# --no-dev deja fuera ClaudeAgent, en Azure el modelo es Foundry
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY demo_agente ./demo_agente

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:${PATH}" \
    HOST=0.0.0.0
ARG APP=backend
ENV APP=${APP}

CMD ["sh", "-c", "python -m demo_agente.${APP}"]
