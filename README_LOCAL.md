# 本机试用

双击 `start-storysage.cmd`，进入原版 StorySage 的文字访谈。
看到 `User:` 后输入回答并按回车。结束时按 Ctrl+C；若停留在输入提示处，再按一次回车。
请等待后续写作和保存完成，不要立即关闭窗口。

当前配置：

- 聊天、记录与写作：MiniMax-M2.7，使用 `.env` 中的 MiniMax 订阅 Key。
- 记忆与问题检索：本机 CPU 上的 multilingual-e5-small 量化模型。
- 模型和分词文件约 135.4 MB，已下载到 `models/multilingual-e5-small`；运行时不下载模型，不调用向量 API。
- 检索仍使用原来的 FAISS 流程，向量维度随模型改为 384。
- 原来的访谈角色、提示词、议程及写作流程保留。这个原版是自传访谈，尚未接入项目的照片或 RFID。
- `local_demo` 对应的历史保存在 `logs/local_demo`，生成正文保存在 `data/local_demo`。

不想沿用历史时，在项目文件夹的终端运行：

```powershell
.\.venv\Scripts\python.exe src\main.py --mode terminal --user_id another_demo
```

更换用户标识即可；不要使用原版 `--restart` 在 Windows 上清理文件。

## 在另一台机器上安装

在项目目录中使用 Python 3.12 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-local.txt
.\.venv\Scripts\python.exe scripts/download_local_embeddings.py
Copy-Item .env.example .env
```

在 `.env` 中添加或修改以下配置。将 `your_subscription_key` 换成自己的 MiniMax 国内订阅密钥，避免保留重复的同名配置行：

```ini
LLM_PROVIDER=minimax
MINIMAX_API_KEY=your_subscription_key
MINIMAX_BASE_URL=https://api.minimax.cn/v1
MODEL_NAME=MiniMax-M2.7
LLM_TEMPERATURE=1.0
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_DIR=models/multilingual-e5-small
OPENAI_API_KEY=
```

配置完成后双击 `start-storysage.cmd`。向量模型只在首次安装时下载，后续推理在本机完成；MiniMax 聊天调用仍需要联网。

本地模型来源：https://huggingface.co/intfloat/multilingual-e5-small/tree/main/onnx
