# source ~/.bashrc

# # 挂一个外网代理
# export https_proxy="http://10.120.6.220:7890"
# export http_proxy="http://10.120.6.220:7890"
# export HTTPS_PROXY="http://10.120.6.220:7890"
# export HTTP_PROXY="http://10.120.6.220:7890"

# # 需要保证这个路径是一个纯code的path，output中不含文件
# CODE_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data
# CONFIG_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data/config/config.yaml
# OPENCLAW_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data/config/init_openclaw.json

# # 输出存储的文件路径
# RES_PATH=/mnt/afs_toolcall/yaotiankuo/data/output_test
# mkdir -p $RES_PATH

# cd ~
# cp -r $CODE_PATH ./

# # 建立软连接，镜像内将输出放在output中，实际会连接到RES_PATH，确保各镜像之间不相互影响
# cd openclaw_gen_data

# ln -sf $RES_PATH ./output

# python3 -m pip install --upgrade pip
# python3 -m pip install -r requirements.txt

# # 启动gateway，日志输出到~/.openclaw-gateway.log
# nohup openclaw gateway run > ~/.openclaw-gateway.log 2>&1 &

# cp $CONFIG_PATH ./config/config.yaml
# cp $OPENCLAW_PATH ~/.openclaw/openclaw.json

# COCURRENT_NUM=2

# python3 scripts/init_agents.py \
#     --num-agents $COCURRENT_NUM \
#     --force-recreate \
#     --refresh-tools

# python3 scripts/run_generation.py \
#     --concurrent $COCURRENT_NUM --limit 2


# ================
# cci 启动命令
# ================

# 基础配置
export CONFIG_PATH=""    # config.yaml文件路径
export CONCURRENT_NUM="50"  # 并发数
export OUTPUT_DIR=""     # 输出目录路径
export INTENTS_PER_SESSION="5"
export INTENTS_FILE=""
export APPEND_QUERY_ENABLED="false"
export APPEND_QUERY_FILE=""

# openclaw 使用的模型
export OPENCLAW_MODEL_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export OPENCLAW_MODEL_API_KEY="sk-xxx"
export OPENCLAW_MODEL_NAME="qwen3.6-plus"

# usermodel 使用的模型
export LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export LLM_API_KEY="sk-xxx"
export LLM_MODEL_NAME="qwen3.6-plus"

# openclaw 搜索配置（可选，启用搜索功能需要）
export OPENCLAW_SEARCH_PROVIDER="serper"
export OPENCLAW_SEARCH_API_KEY="xxx"
export OPENCLAW_SEARCH_BASE_URL="https://google.serper.dev"

# 一键启动
bash scripts/start_generation_in_container.sh

# sleep 0.5d