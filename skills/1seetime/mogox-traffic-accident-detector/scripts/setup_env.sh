#!/bin/bash
set -e

cd "$(dirname "$0")/.."

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 配置国内 pip 镜像
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装依赖
pip install -r requirements.txt

echo "环境设置完成！"
echo "激活虚拟环境: source .venv/bin/activate"
