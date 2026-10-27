FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/data

EXPOSE 18610

ENV MUSE_HOST=0.0.0.0
ENV MUSE_PORT=18610
ENV MUSE_DRIVER=browser
ENV MUSE_HEADLESS=true

CMD ["uvicorn", "muse_router.main:app", "--host", "0.0.0.0", "--port", "18610"]
