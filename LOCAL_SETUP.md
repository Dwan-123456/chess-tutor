# 本机运行

双击桌面的 Chess Annotator 快捷方式，自动启动本地网页服务并打开浏览器。
地址：http://127.0.0.1:8765。重复启动会打开已有服务。

已安装 Python 独立环境、网页依赖和 Stockfish 19 Windows x64 通用版。
Stockfish 在分析棋谱时按需启动。没有下载任何语言模型。
启动器自动读取项目根目录唯一的 `*apiKey*.csv`，从 `apiKey` 和
`openAiCompatible` 两行取得密钥和接口地址，并调用
`deepseek-v4.1-flash-1017` 生成中文解说。CSV 不进入 Git，也不会发给浏览器。
如果没有 CSV 且没有其他配置，启动器进入仅引擎分析模式。

开发：使用 VS Code 打开此目录，解释器为 `.venv/Scripts/python.exe`。
也可以运行 `.venv/Scripts/python.exe launch.py`。
依赖快照：`requirements-local.lock.txt`；服务日志：`runtime/server.log`。

DeepSeek 已在 `webapp/llm.py` 接入阿里云兼容接口。
`.env.example` 提供可选配置；启动器自动读取 `.env`，环境变量优先于 CSV。
默认关闭二次模型事实检查，以减少延迟与额外调用；可自行启用。
界面费用与每日预算使用估算单价，不代表账单；实际费用以阿里云为准，
可用 PRICE_IN_PER_M、PRICE_OUT_PER_M 配置每百万 token 的美元价格。
更换 CSV 或模型后需要重启服务。服务只绑定 127.0.0.1，不向局域网开放。
请勿提交真实密钥、模型文件或运行日志。

原始项目：https://github.com/shashank-v-98/chess-annotator（保留上游 MIT 许可证）。
Stockfish：https://github.com/official-stockfish/Stockfish/releases/tag/sf_19（GPL）。
本机二进制、虚拟环境和日志不进入 Git；GitHub 新建仓库及推送待登录后处理。

## 本地历史记录
完整成功的 DeepSeek 分析自动保存为 `history/<分析标识>.json`。记录含棋谱主线、原始模型输出、规则指纹、请求编号和原始用量；不保存 API 密钥。
相同起始局面与主线、规则和提示词文件、引擎版本、深度、模型及事实检查配置一致时复用。规则或提示词代码变化自动失效；旧记录保留。未完成、出错或损坏的记录不会复用。
复用时本次费用为零，界面不增加历史记录标签。此前未落盘的分析需要首次重新生成。

引擎配置：Depth 30，Threads 10，Hash 8192MB，同时最多分析 1 盘棋。可通过 ENGINE_DEPTH、ENGINE_THREADS、ENGINE_HASH_MB、MAX_CONCURRENT_GAMES 设置；搜索参数变化自动使旧历史记录不再匹配。

## 网页引擎设置
右上角 Setting 打开三项滑动条，参数自动保存到 `runtime/engine-settings.json`，后续重启仍生效。取值范围：线程 1 到本机逻辑处理器数，Hash 256MB 到 32GB（每档 256MB），深度 1 到 40。
设置仅影响下一次载入棋局；当前棋局与重试使用载入时的参数快照。不同参数的历史分析不会混用。
