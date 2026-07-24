# Production API image. Uses standard CPython from the official Python image.
FROM python:3.11-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip build

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel

FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/dist/*.whl ./
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl

EXPOSE 8000
CMD ["science-companion", "api", "--host", "0.0.0.0", "--port", "8000"]
