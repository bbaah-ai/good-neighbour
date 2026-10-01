FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY good_neighbour ./good_neighbour
COPY data ./data
COPY push_endpoint ./push_endpoint

RUN pip install --no-cache-dir ".[gcp,server]" \
    && useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080
CMD ["uvicorn", "push_endpoint.main:api", "--host", "0.0.0.0", "--port", "8080"]
