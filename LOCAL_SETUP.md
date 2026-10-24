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

## CPU 与引擎调度

服务使用一个专用工作队列和一个常驻 Stockfish 进程，同时只执行一盘棋的搜索。退出棋局或断开连接时发送 UCI `stop`，收到停止结果后才会开始下一盘，取消时的中间评分不会写入结果。完整分析仍搜索到用户选择的深度，没有增加单步时间截断。

相同配置的后续棋局复用线程池和已分配的 Hash；每盘开始用 `ucinewgame` 清理上一盘的搜索状态，盘内连续局面保留 Hash。参数变化只在下一盘应用；需要重建线程池时，先缩小旧 Hash，最后再分配目标 Hash，减少重复的大内存分配。服务关闭时释放引擎和内存。Hash 是置换表大小，进程还需要模型、线程栈等额外内存。

Windows 混合架构 CPU 可以使用本机实测的核心绑定。配置保存在 `runtime/cpu-scheduling.json`，按引擎文件、CPU 拓扑、线程数和 Hash 匹配；只有相对自动调度实测吞吐量提升至少 3% 的绑定才启用。未校准或不支持的配置使用 Stockfish `NumaPolicy=auto`。本机 13600KF 的 10 线程、8GB Hash 已校准为优先独立物理核，避免部分搜索线程共享同一性能核的超线程资源。

重新校准请在没有棋局分析时运行（只运行本地 Stockfish，不调用 DeepSeek）：

```powershell
.\.venv\Scripts\python.exe -m webapp.benchmark_engine --threads 10 --hash-mb 8192
```

基准交替测试自动调度和物理核优先方案，在两个局面上重复四轮，每个局面两秒。测量的是搜索节点吞吐量，并非固定深度或整盘分析耗时的保证。

## 在线七子残局库

默认启用 Lichess 七子 Syzygy 查询，不需要密钥，不下载完整残局库。应用层对棋谱中双方合计不超过七枚棋子（包含两个王）的局面查询胜和负、推荐着法及 DTZ；Stockfish 仍在本地搜索，这不是 Stockfish 内部每个搜索分支的远程探测。

结果缓存到 `history/tablebase/<局面标识>.json`，后续相同局面直接读本地。缓存标识包含五十步规则计数；不把不同计数的局面混用。带王车易位权、已经终局、可申诉三次重复的局面不使用在线结论。查询超时、失败或限流时保留 Stockfish 结果，限流后暂停联网一分钟；缺少应有的残局库结果时，不把整盘分析存为可完整复用的历史记录。

查询成功时，网页单独显示 Lichess 来源，评估条及解说采用精确胜和负。DTZ 是朝吃子、动兵或终局推进的残局指标，可能取整或带特殊编码，不能直接解释为将杀步数。两侧均有精确结论时，评级按是否损失理论胜和负判断；保持胜势但不同于首选着法不再仅因评分变化而判大漏。

每次有效局面的查询都在 `history/tablebase/requests.jsonl` 追加耗时记录，含 `source`（network/cache/cooldown）、`elapsed_ms`（整次查找时间）、`network_ms`（仅联网阶段）、HTTP 状态与是否成功。只向 Lichess 发送局面 FEN，不发送 API Key、棋谱注释或玩家姓名。可用 `TABLEBASE_ENABLED=false` 关闭，重启服务后生效。
