# ---------------------------------------------------------------
# Estágio 1 — BUILDER
# Imagem -dev da Chainguard: tem pip e toolchain pra instalar deps.
# ---------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev AS builder

WORKDIR /app

COPY --chown=65532:65532 requirements.txt .

RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------
# Estágio 2 — RUNTIME
# Imagem final da Chainguard: sem shell, sem pip, sem gerenciador
# de pacotes, roda como usuário nonroot — superfície de ataque
# mínima e (quase) zero CVEs.
# ---------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest

WORKDIR /app

COPY --from=builder --chown=65532:65532 /app/venv /app/venv
COPY --chown=65532:65532 app.py index.html ./

ENV PATH="/app/venv/bin:$PATH"

EXPOSE 8080

# gunicorn em vez do dev server do Flask — servidor WSGI de produção
ENTRYPOINT ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--access-logfile", "-", "app:app"]
