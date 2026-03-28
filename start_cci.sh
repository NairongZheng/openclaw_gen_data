source ~/.bashrc

# 挂一个外网代理
export https_proxy="http://10.120.6.220:7890"
export http_proxy="http://10.120.6.220:7890"
export HTTPS_PROXY="http://10.120.6.220:7890"
export HTTP_PROXY="http://10.120.6.220:7890"

# 需要保证这个路径是一个纯code的path，output中不含文件
CODE_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data
CONFIG_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data/config/config.yaml
OPENCLAW_PATH=/mnt/afs_toolcall/yaotiankuo/data/openclaw_gen_data/config/init_openclaw.json

# 输出存储的文件路径
RES_PATH=/mnt/afs_toolcall/yaotiankuo/data/output_test
mkdir -p $RES_PATH

cd ~
cp -r $CODE_PATH ./

# 建立软连接，镜像内将输出放在output中，实际会连接到RES_PATH，确保各镜像之间不相互影响
cd openclaw_gen_data

ln -sf $RES_PATH ./output

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# 启动gateway，日志输出到~/.openclaw-gateway.log
nohup openclaw gateway run > ~/.openclaw-gateway.log 2>&1 &

cp $CONFIG_PATH ./config/config.yaml
cp $OPENCLAW_PATH ~/.openclaw/openclaw.json

COCURRENT_NUM=2

python3 scripts/init_agents.py \
    --num-agents $COCURRENT_NUM \
    --force-recreate \
    --refresh-tools

python3 scripts/run_generation.py \
    --concurrent $COCURRENT_NUM --limit 2