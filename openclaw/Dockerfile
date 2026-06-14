# Use the known-working rollback image as the base. It already contains a
# functioning openclaw@2026.4.5 install with all native/channel deps resolved.
FROM openleg-openclaw:rollback

# Update MCP OpenLEG server.
WORKDIR /opt/mcp-openleg-server
COPY --chown=node:node mcp-openleg-server/package.json ./
RUN npm install --production
COPY --chown=node:node mcp-openleg-server/server.mjs ./

# Overlay current config and entrypoint.
USER root
COPY config/ /opt/openclaw-config/
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh \
    && chown -R node:node /opt/mcp-openleg-server /opt/openclaw-config /opt/entrypoint.sh

USER node
WORKDIR /home/node

EXPOSE 18789

ENTRYPOINT ["/opt/entrypoint.sh"]
