FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controller.py .

# Non-root user (matches securityContext in deployment YAML)
RUN useradd -u 1000 -m controller
USER 1000

EXPOSE 8082

ENTRYPOINT ["python", "-u", "controller.py"]
