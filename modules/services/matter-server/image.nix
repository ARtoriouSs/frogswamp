{
  # Version + digest, so a reinstall pulls exactly this server. matterjs-server replaces python-matter-server
  # (same WebSocket API for HA; the python image stopped at 8.1.0)
  image = "ghcr.io/matter-js/matterjs-server:1.5.2@sha256:47bc4d8354baf4775c1daf87b02b61123b1b61feeb3b2eadb17c610dbbda9a1c";
}
