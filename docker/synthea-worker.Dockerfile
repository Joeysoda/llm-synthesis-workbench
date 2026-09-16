FROM python:3.12-slim

# The current python:3.12-slim base uses Debian Trixie, which provides JDK 21.
# Synthea is compiled with Java 17 compatibility and runs on this supported LTS runtime.
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jdk-headless
WORKDIR /app
COPY upstream/synthea /opt/synthea
COPY synthea_worker /app/synthea_worker
RUN cd /opt/synthea && ./gradlew --no-daemon shadowJar
RUN pip install --no-cache-dir fastapi==0.116.1 "uvicorn[standard]==0.35.0" pydantic==2.11.7

ENV PYTHONPATH=/app
ENV RUNTIME_ROOT=/app/runtime
ENV SYNTHEA_JAR=/opt/synthea/build/libs/synthea-with-dependencies.jar
CMD ["uvicorn", "synthea_worker.app:app", "--host", "0.0.0.0", "--port", "18200"]
