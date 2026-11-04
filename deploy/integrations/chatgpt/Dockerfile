# A deployment composition: the standard Core image is built independently.
ARG CORE_IMAGE
FROM python:3.12-slim AS tunnel
WORKDIR /download
ARG TUNNEL_VERSION=0.0.19
ARG TUNNEL_SHA256=9ee9448592345a9dd1517fb79791ab54d24f051142eb92bcbe8cd355effc9142
RUN python -c 'import hashlib,io,os,urllib.request,zipfile; v=os.environ["TUNNEL_VERSION"]; data=urllib.request.urlopen(f"https://github.com/openai/tunnel-client/releases/download/v{v}/tunnel-client-v{v}-linux-amd64.zip").read(); assert hashlib.sha256(data).hexdigest()==os.environ["TUNNEL_SHA256"], "Tunnel checksum mismatch"; zipfile.ZipFile(io.BytesIO(data)).extractall("/tunnel")' \
    && chmod +x /tunnel/tunnel-client /tunnel/cloudflared

FROM ${CORE_IMAGE}
COPY --from=tunnel --chown=inkcre:inkcre /tunnel/ /opt/inkcre-chatgpt/
COPY --chown=inkcre:inkcre launcher.py /opt/inkcre-chatgpt/launcher.py
CMD ["python", "/opt/inkcre-chatgpt/launcher.py"]
