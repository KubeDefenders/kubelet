FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY frontend/ ./frontend/
COPY config/ ./config/

# Non-root user
RUN useradd -r -u 1000 kubeddos
USER 1000

ENV FLASK_APP=frontend/app.py
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]
