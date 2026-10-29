# MAGP_kada 開発用（手元 PC 専用）。実機 Jetson では使わない。
# 環境の中身は scripts/setup.sh が唯一の定義。
FROM osrf/ros:humble-desktop

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# setup.sh がリポジトリ相対で requirements.txt を見つけるための配置
WORKDIR /opt/magp-setup
COPY requirements.txt /opt/magp-setup/requirements.txt
COPY scripts/setup.sh /opt/magp-setup/scripts/setup.sh
# PC 用の torch。CUDA が要るときは --build-arg TORCH_FLAVOR=cu121
ARG TORCH_FLAVOR=cpu
RUN chmod +x /opt/magp-setup/scripts/setup.sh \
    && MAGP_TORCH_FLAVOR=${TORCH_FLAVOR} bash /opt/magp-setup/scripts/setup.sh

# ホストと同じ UID/GID の一般ユーザーを作る。
# root で動かすと、マウントした /ws に build/ install/ log/ が root 所有で作られてしまう。
ARG USER_UID=1000
ARG USER_GID=1000
RUN (getent group ${USER_GID} || groupadd -g ${USER_GID} magp) \
    && (getent passwd ${USER_UID} && userdel -r $(getent passwd ${USER_UID} | cut -d: -f1) || true) \
    && useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash magp \
    && apt-get update && apt-get install -y --no-install-recommends sudo \
    && echo "magp ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/magp \
    && rm -rf /var/lib/apt/lists/*

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ソースは実行時にマウントする（イメージに焼き込まない）
USER magp
WORKDIR /ws
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
