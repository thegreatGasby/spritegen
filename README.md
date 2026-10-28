# spritegen — 本地精灵图工作台

对标 spritegen.ai：**描述 → 生成 → 循环预览 → 下载透明 PNG 精灵表**。后端全走 Qwen 网关，
前端是一个仿该站的单屏工作台。

两条链路，覆盖两个诉求：
1. **文字/参考 → 2D 精灵图**：`qwen-image` 出 master 静帧（图片模式一次直出多帧网格）。
2. **精灵/master → 动作帧**：由同一张 master，用 `wan2.2-i2v-plus` 出 idle、walk、run 等动作，
   抽帧、抠像、跨帧注册、打包成精灵表。

## 目录

```
backend/
  cli.py                命令行入口
  spritegen/
    qwen.py             Qwen 网关客户端（chat / generate_image / generate_video）
    prompts.py          提示词库：12 风格 / 4 视角 / 9 动作 / 网格规则 / 品红契约 / LLM system
    imageops.py         抠像(品红+绿)、去地、剪屑、量化、跨帧注册、等分切片
    packer.py           sheet.png + preview.gif + atlas/Godot 元数据
    pipeline.py         4 段编排
  server/app.py         FastAPI 服务（后台作业 + 轮询）
frontend/               Vue 3 + Vite 工作台
out/                    生成产物（characters/ jobs/）
run.sh                  一键起后端
```

## 环境

- 后端跑在项目自带的虚拟环境 `env/`（依赖见根目录 `requirements.txt`）。`run.sh` 会依次找
  `env/`、`venv/`、`.venv/`，都没有才回退到 conda `webui`；也可用 `PY=/path/to/python ./run.sh` 指定。
- 系统 `ffmpeg` 必需；前端用 `bun` 构建。

首次准备：

```bash
cd /data/spritegen
python3 -m venv env
env/bin/pip install -r requirements.txt
cp .env.example .env          # 填入 QWEN_MAAS_KEY
```

## 配置

把 key 填进 `/data/spritegen/.env`（模板见 `.env.example`），后端与 CLI 会自动加载；
真实环境变量优先于该文件。

```
QWEN_MAAS_KEY=sk-...
# QWEN_MAAS_BASE=https://maas.qianwenaiapi.com
```

## 启动

生产（后端同时托管已构建的前端，单一端口）：

```bash
cd /data/spritegen
./run.sh                      # 读取 .env -> http://127.0.0.1:8760
```

开发（前端热更新，代理到后端）：

```bash
cd /data/spritegen
./run.sh &                    # 后端 :8760
cd frontend && bun install && bun run dev   # 前端 :5173，/api 与 /files 代理到 :8760
```

首次或改动前端后若要用生产模式，先构建：`cd frontend && bun run build`。

## 网页用法

1. 在「描述」里写角色（可带动作），选**视角**（横版/正面/背面/四分之三）、**风格**（12 种）、
   **朝向**（朝左/朝右）、**动作**（待机/行走/奔跑/跳跃/攻击/施法/受击/死亡/飞行）、
   **模式**（视频=动作更稳 / 图片=更快）、**帧数**（6/8/12）、**格子**（HD 或 16–128 px）。
2. 点「生成」或 `Ctrl+Enter`。等待约 1–2 分钟，直播间会循环播放成品。
3. 下载 `sheet.png`（透明，6/8 帧一行、12 帧 4×3）与 `preview.gif`。
4. 想「一个角色出多个动作」：先用「只生成角色」得到 master，再从**角色库**里选中它，
   换不同**动作**连续生成——都复用同一张 master，形象一致。

## 命令行用法

```bash
cd /data/spritegen/backend
PY=/home/newworld/.conda/envs/webui/bin/python3
# key 从 /data/spritegen/.env 自动加载（也可临时 export）

$PY cli.py presets                                   # 列出可选参数
$PY cli.py character --prompt "持剑骑士，深色像素风，横版朝右" --out out/knight
$PY cli.py action --character out/knight/character.json --action walk --mode video
$PY cli.py make --prompt "粉红史莱姆，可爱像素风" --action idle --mode image --frames 8
# 兜底：--no-llm 跳过 LLM 分类，直接用 --style/--view/--facing/--action
```

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/presets` | 风格/视角/朝向/动作/帧数/格子选项 |
| POST | `/api/characters` | 生成角色 master 静帧 → `{job_id}` |
| POST | `/api/actions` | 生成一个动作（可带 `character_id` 复用 master）→ `{job_id}` |
| GET  | `/api/jobs/{id}` | 作业状态：`status/stage/progress/result/error` |
| GET  | `/api/characters` | 角色库列表 |
| GET  | `/api/gallery` | 最近生成的精灵表 |
| GET  | `/files/...` | 产物（sheet/preview/帧/master） |
