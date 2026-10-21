{
  # Version + digest, so a reinstall pulls exactly this engine. From Docker Hub, not ghcr.io:
  # the route to GitHub loses packets here and pulls time out (same image, same digest)
  image = "docker.io/martinbjeldbak/acestream-http-proxy:3.4.0@sha256:06f160a777bfe84029842e0d4c0e67d799a459b33d71ce16c836081fed8e2b2e";
}
