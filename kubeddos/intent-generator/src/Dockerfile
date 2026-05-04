FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY intent_generator.py .

# Non-root user (matches securityContext in deployment YAML)
RUN useradd -u 1000 -m generator
USER 1000

EXPOSE 8081

ENTRYPOINT ["python", "-u", "intent_generator.py"]
