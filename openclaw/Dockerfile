FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install openclaw globally
RUN npm install -g openclaw@latest

# Install MCP OpenLEG server
WORKDIR /opt/mcp-openleg-server
COPY mcp-openleg-server/package.json ./
RUN npm install --production
COPY mcp-openleg-server/ ./

# Set up config directory and entrypoint
COPY config/ /opt/openclaw-config/
COPY entrypoint.sh /opt/entrypoint.sh

# Permissions
RUN mkdir -p /home/node/.openclaw \
    && chown -R node:node /home/node /opt/mcp-openleg-server /opt/openclaw-config /opt/entrypoint.sh \
    && chmod +x /opt/entrypoint.sh

USER node
WORKDIR /home/node

EXPOSE 18789

ENTRYPOINT ["/opt/entrypoint.sh"]
