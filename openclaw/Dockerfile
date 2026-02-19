FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install openclaw globally
RUN npm install -g openclaw@latest

# Install MCP BadenLEG server
WORKDIR /opt/mcp-badenleg-server
COPY mcp-badenleg-server/package.json ./
RUN npm install --production
COPY mcp-badenleg-server/ ./

# Set up config directory and entrypoint
COPY config/ /home/node/.openclaw/
COPY entrypoint.sh /opt/entrypoint.sh

# Permissions
RUN chown -R node:node /home/node /opt/mcp-badenleg-server /opt/entrypoint.sh \
    && chmod +x /opt/entrypoint.sh

USER node
WORKDIR /home/node

EXPOSE 18789

ENTRYPOINT ["/opt/entrypoint.sh"]
