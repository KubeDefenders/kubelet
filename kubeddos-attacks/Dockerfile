FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy attack scripts and configs
COPY attacks/ ./attacks/
COPY config.py ./config.py
COPY frontend/ ./frontend/

# Results directory
RUN mkdir -p /app/results

# Non-root user
RUN useradd -r -u 1000 kubeddos
USER 1000

ENV PYTHONUNBUFFERED=1
ENV ATTACKS_DIR=/app/attacks
ENV CONFIGS_DIR=/app/attacks/configs
ENV RESULTS_DIR=/app/results

EXPOSE 5001

CMD ["python", "frontend/app.py"]
