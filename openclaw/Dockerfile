FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y git curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install openclaw globally
RUN npm install -g openclaw@latest

# Install MCP OpenLEG server
WORKDIR /opt/mcp-openleg-server
COPY mcp-openleg-server/package.json ./
RUN npm install --production
COPY mcp-openleg-server/ ./

# Stage config (entrypoint copies to .openclaw so volume-mounted data persists)
COPY config/ /opt/openclaw-config/
COPY workspace/ /opt/openclaw-workspace/
COPY entrypoint.sh /opt/entrypoint.sh

# Permissions
RUN chown -R node:node /home/node /opt/mcp-openleg-server /opt/openclaw-config /opt/entrypoint.sh \
    && chmod +x /opt/entrypoint.sh

USER node
WORKDIR /home/node

EXPOSE 18789

ENTRYPOINT ["/opt/entrypoint.sh"]
