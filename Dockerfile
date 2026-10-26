FROM python:3.12-slim

RUN useradd --create-home --uid 10001 mcp
WORKDIR /app
COPY pyproject.toml README.md ./
COPY technova_mcp ./technova_mcp
RUN pip install --no-cache-dir .

USER mcp
WORKDIR /home/mcp
ENV TECHNOVA_DB=/home/mcp/data/technova.db TECHNOVA_TOKENS=/home/mcp/data/tokens.json
RUN mkdir -p /home/mcp/data
VOLUME ["/home/mcp/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8765), 2)"
CMD ["technova-mcp", "serve", "--http", "--host", "0.0.0.0", "--port", "8765"]
