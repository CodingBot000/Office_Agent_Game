FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 officeagent

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1.0" \
    "pydantic-settings>=2.6,<3.0" \
    "uvicorn[standard]>=0.34,<1.0"

COPY app ./app
RUN mkdir -p /app/data && chown -R officeagent:officeagent /app

USER officeagent
EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
