FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY csirt ./csirt
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home csirt && mkdir /data && chown csirt:csirt /data
USER csirt
ENV CSIRT_DB_PATH=/data/csirt.sqlite3
EXPOSE 8000
CMD ["uvicorn", "csirt.api:app", "--host", "0.0.0.0", "--port", "8000"]
