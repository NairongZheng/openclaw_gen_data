FROM ubuntu:22.04

ARG TARGETARCH
ARG NODE_VERSION=24
ARG PYTHON_VERSION=3.12
ARG CONDA_ENV_NAME=dev
ARG OPENCLAW_INSTALL_URL=https://openclaw.ai/install.sh
ARG MINICONDA_BASE_URL=https://repo.anaconda.com/miniconda
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    OPENCLAW_NO_PROMPT=1 \
    OPENCLAW_NO_ONBOARD=1 \
    OPENCLAW_AUTO_START_GATEWAY=1 \
    OPENCLAW_GATEWAY_LOG=/root/.openclaw/gateway.log \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    no_proxy=${no_proxy} \
    NVM_DIR=/root/.nvm \
    CONDA_DIR=/opt/miniconda3 \
    PATH=/opt/node/bin:/opt/miniconda3/bin:/root/.local/bin:$PATH

SHELL ["/bin/bash", "-lc"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    cloc \
    cmake \
    curl \
    git \
    htop \
    iproute2 \
    iputils-ping \
    lsof \
    net-tools \
    netcat-openbsd \
    openssh-client \
    openssh-server \
    tmux \
    socat \
    telnet \
    tree \
    vim \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/run/sshd /workspace /root/.openclaw

RUN git clone https://github.com/nvm-sh/nvm.git "$NVM_DIR" \
    && cd "$NVM_DIR" \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && NODE_REAL_DIR="$(find "$NVM_DIR/versions/node" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)" \
    && ln -sfn "$NODE_REAL_DIR" /opt/node

COPY requirements.txt /tmp/requirements.txt

RUN set -euxo pipefail \
    && case "$TARGETARCH" in \
        amd64) MINICONDA_ARCH="x86_64" ;; \
        arm64) MINICONDA_ARCH="aarch64" ;; \
        *) echo "Unsupported TARGETARCH: $TARGETARCH" && exit 1 ;; \
    esac \
    && wget -O /tmp/miniconda.sh "${MINICONDA_BASE_URL}/Miniconda3-latest-Linux-${MINICONDA_ARCH}.sh" \
    && bash /tmp/miniconda.sh -b -p "$CONDA_DIR" \
    && rm -f /tmp/miniconda.sh \
    && "$CONDA_DIR/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main \
    && "$CONDA_DIR/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r \
    && "$CONDA_DIR/bin/conda" create -y -n "$CONDA_ENV_NAME" "python=${PYTHON_VERSION}" \
    && "$CONDA_DIR/bin/conda" run -n "$CONDA_ENV_NAME" python -m pip install --upgrade pip \
    && "$CONDA_DIR/bin/conda" run -n "$CONDA_ENV_NAME" python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && "$CONDA_DIR/bin/conda" clean -afy

RUN export OPENCLAW_NO_PROMPT=1 OPENCLAW_NO_ONBOARD=1 \
    && curl -fsSL "$OPENCLAW_INSTALL_URL" | bash \
    && OPENCLAW_BIN="$(find "$NVM_DIR/versions/node" -path '*/bin/openclaw' -type f | sort | tail -n 1)" \
    && test -n "$OPENCLAW_BIN" \
    && test -x "$OPENCLAW_BIN" \
    && ln -sfn "$OPENCLAW_BIN" /usr/local/bin/openclaw \
    && openclaw --version >/dev/null 2>&1 || true

RUN openclaw onboard --non-interactive --accept-risk --flow quickstart --mode local \
    --auth-choice skip --skip-channels --skip-search --skip-skills --skip-ui --skip-daemon --skip-health --json

RUN cat <<'EOF' >/etc/profile.d/openclaw-env.sh
export NVM_DIR="/root/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate dev
EOF

COPY . /workspace
RUN chmod +x /workspace/scripts/start_generation_in_container.sh

WORKDIR /workspace

CMD ["/bin/bash", "-l"]
