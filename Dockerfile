FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY agent_harness ./agent_harness
COPY scripts ./scripts
RUN pip install --no-cache-dir '.[web]' && useradd --uid 10001 --create-home agent && mkdir /app/data && chown agent:agent /app/data
USER agent
EXPOSE 8000
CMD ["python", "scripts/run_web.py", "--host", "0.0.0.0"]
