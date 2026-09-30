# RVC Studio 0.2.0 测试记录

日期：2026-09-29。环境：Windows Server 2022 x64、Python 3.11.15，无 NVIDIA GPU 或实体麦克风。

## 已通过

| 项目 | 结果与范围 | 证据 |
| --- | --- | --- |
| 单元及进程协议 | 22/22；部分使用明确的模拟依赖 | `tests/test_studio.py`、`tests/test_integration.py`、`build/build-integrated.log` |
| GUI | 四页初始化、默认窗口按钮边界、中文文件名导入、清空旧索引、恢复预设 | `build/gui-check.json` |
| 打包主程序 | 从 System32 工作目录启动，GUI 及内嵌资源可用 | `build/exe-smoke.json` |
| 官方驱动归档和签名 | ZIP SHA-256 匹配；官方 EXE 及 CAT 的 Authenticode 均 Valid | `build/bundled-driver-signatures.json` |
| 真实驱动安装 | 官方安装器退出 0，实际创建 ROOT\MEDIA\0000，设备错误码 0；报告需要重启 | `build/driver-install-check.json` |
| 打包安装助手 | 自检、包校验、实际设备检测通过；再次调用安装复用已有驱动，未重复安装 | `build/helper-*.ini` |
| 统一安装流程成功分支 | 单独测试安装包使用模拟引擎/驱动助手；真实 GUI 和助手文件部署后能启动，并能卸载 | `build/integrated-installer-check.json`、`build/installed-v02-smoke.json` |
| 统一安装流程失败分支 | 模拟下载失败，含中文和百分号的错误可传递；安装退出 7，不部署主程序 | `build/integrated-flow-failure.log` |
| 交付安装包拒绝错误引擎 | 真实助手拒绝不符合官方大小的 ZIP，安装退出 7，不部署主程序 | `build/shipping-invalid-archive.log` |
| 官方 Windows 引擎包内容 | 远程 Range 读取 ZIP 目录及关键源码，35397 项、展开约 7.53 GB；基础模型存在，源码规范化换行后与固定哈希一致 | `build/zip-inspection/report.json`、`build/zip-inspection/entries.json` |

GUI 导入测试使用非真实模型数据，只证明文件操作。安装流程的成功测试使用明确的模拟组件，不能视为完整引擎安装验收。
远程归档检查确认了 Python、ContentVec、RMVPE、FCPE 及 VC++ 运行库的目录项，并实际读取了关键引擎源码；并未完整下载所有内容。

单元测试还覆盖损坏缓存清理后重试、正确 Range 续传、取消、路径穿越与 Windows 设备路径拒绝、
暂存解压清理、换行差异、驱动返回成功但无设备时拒绝误报、引擎依赖失败不写入就绪配置、
无索引禁用检索、缓冲溢出、监听隔离、设备重新编号和虚拟端点识别。

## 尚未通过实机验收

- **完整引擎安装**：本机未完成约 4.93 GB 官方包下载、整体 SHA-256、完整解压和实际依赖导入。
  安装助手会在用户机器执行这些检查，失败时停止安装并报告错误。
- **实际音频环回**：驱动已安装，但当前未重启的远程会话只列出 WDM-KS 端点，缺少正常 CABLE Input / CABLE Output 端点。
  合成音环回检查因此未执行，不能记为通过。见 `build/audio-loopback-check.json`。没有擅自重启电脑。
- **真实推理**：模型加载、文件转换、录音转换、RMVPE/FCPE、实时音质、RTX 3070 延迟及长时间稳定性。
- **目标系统与通话**：Windows 10/11 实机、微信/Discord/QQ、设备热插拔、安全启动与内存完整性兼容。
- **完全离线包及应用代码签名**：本版本是在线安装器；应用 EXE 未商业签名，官方驱动签名保留且已验证。
- **截图检查**：执行环境无法正常抓屏；已检查控件初始化与几何布局。

测试中实际安装了普通版 VB-CABLE，未修改测试签名模式、证书信任、安全启动或默认音频设备。
安装流程测试仅卸载其隔离测试目录中的应用；没有卸载共享音频驱动。

## 交付

`dist/RVCStudio-Setup-0.2.0-online.exe`，约 22 MB，旁边 `.sha256` 为完整性校验。
包含中文 GUI、适配代码、安装助手及官方虚拟麦克风驱动；统一安装过程中获取引擎。
用户自备 `.pth` 与可选 `.index`。当前为集成 Beta，尚不能宣称已完成目标硬件的端到端验收。
