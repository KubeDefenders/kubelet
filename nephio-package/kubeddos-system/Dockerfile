FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the controller source
COPY nephio_controller.py .

# Patch in-cluster config: replace load_kube_config() with load_config() so the
# controller auto-detects whether it's running inside a pod or locally.
RUN sed -i \
    's/kubernetes\.config\.load_kube_config()/kubernetes.config.load_config()/g' \
    nephio_controller.py

# Non-root user for security compliance
RUN useradd -u 1000 -m controller
USER 1000

# kopf health endpoint
EXPOSE 8080

# Default entrypoint — overridden by the Deployment spec which adds --liveness
# and the controller path explicitly.
ENTRYPOINT ["kopf", "run", "--all-namespaces"]
CMD ["/app/nephio_controller.py"]
