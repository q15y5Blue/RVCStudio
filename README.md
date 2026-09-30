# RVC Studio 0.3.0

根据本目录的 Applio RVC v2 中文实时变声指南制作的 Windows 桌面软件，提供中文界面、实时变声与文件转换，支持导入自己的模型。

## 安装

推荐使用**离线全量安装包** `dist/RVCStudio-0.3.0-offline.zip`（单个压缩包，约 4.7 GiB，属构建产物、不入库）：
右键“全部解压”到同一文件夹，再双击里面的 `RVCStudio-Setup.exe` 启动中文安装向导，**全程不联网**，
已内置约 4.93 GB 官方 Applio 3.6.5 引擎、两把自然普通话女声模型（主 `ChineseFemale` 为标准 RVC v2/200ep，
备 `ChineseFemale_HQ` 为 Ov2/350ep）以及 VB-CABLE 虚拟麦克风；无需另外安装或打开 Applio、VB-CABLE，也无需再下载模型。
注意 `RVCStudio-Setup.exe` 必须和 `engine.bin.001～004` 放在同一文件夹，不要单独复制出来。
建议预留约 25 GB 可用空间，安装驱动时需要一次管理员授权，首次安装驱动后按提示重启。

> 为什么是压缩包而不是单个 exe：Windows 无法运行总体积超过约 4 GB 的单个自解压可执行文件
> （会提示“此应用无法在你的电脑上运行”，加载器错误 193），引擎本身已达 4.93 GB，
> 因此离线版以“一个 ZIP + 解压后的文件夹”分发，仅多一次解压，安装过程完全相同。

旧的在线统一安装程序 `RVCStudio-Setup-0.2.0-online.exe` 仍保留：运行时才下载引擎、不内置模型。

目标为 Windows 10/11 x64；实时模式需要 NVIDIA CUDA 显卡及显卡驱动（原指南目标 RTX 3070），
不需要自行安装 Python 或 CUDA Toolkit。应用 EXE 尚未做商业代码签名；内置官方驱动保留厂商签名和微软目录签名。

## 使用

1. 安装、按需重启后打开 RVC Studio，软件自动检测引擎和音频设备。
2. 离线版已默认选中内置主女声，可在“① 模型与声音”页一键切换备选女声(HQ)做 A/B 对比；也可导入自己的 `.pth`（可选 `.index`），先用音频文件或 15 秒录音转换试听。
3. 实时页面选择实体麦克风，变声输出选择 `CABLE Input (VB-Audio Virtual Cable)`。
4. 聊天软件的麦克风选择 `CABLE Output (VB-Audio Virtual Cable)`，扬声器选择耳机。
5. 点击开始实时变声；需要自己听见效果时启用耳机监听。

软件检测到普通版 VB-CABLE 时会自动选择其播放端。没有设备时先重启，也可在“环境与驱动”页重新检测/安装。
不改变系统默认音频设备。完整说明见 [USER_GUIDE.txt](studio/USER_GUIDE.txt)。

## 实现与验证范围

- 中文模型导入、参数预设、文件转换、录音试听、实时推理及独立耳机监听。
- 独立后台引擎进程、有界音频缓冲、设备变化检测、停止释放、日志及诊断导出。
- 固定 Applio 3.6.5 官方包及 SHA-256；断点续传、取消、损坏缓存清理、安全暂存解压、真实依赖导入检查。
- 内置未修改的官方普通版 VB-CABLE 包，验证归档及 Windows 签名，调用官方安装器并检查实际 PnP 设备。
- 统一安装向导处理进度、错误、管理员授权和重启；组件失败时不报告安装完成。

本机已验证驱动真实安装、驱动复用、GUI、22 项单元/协议测试，以及使用模拟组件的安装流程。
同时通过远程 ZIP 目录及部分真实文件核对了官方包中的基础模型和引擎源码。
**当前开发服务器没有 NVIDIA GPU 和实体麦克风，完整 4.93 GB 引擎安装及实际变声音质、延迟和通话尚未验收。**
驱动安装后的音频端点环回还需重启和本地 Windows 会话测试。因此保留 Beta 标记。
证据和限制见 [TEST_REPORT.md](TEST_REPORT.md)。

## 来源与许可

VB-CABLE 由 VB-Audio / Vincent Burel 提供，是 donationware。
产品与捐赠：[VB-CABLE](https://vb-audio.com/Cable/)。
本项目按厂商公开页面中普通版随应用分发的条件保留产品标识、原始包及捐赠入口；企业或机构用途遵循对应许可条件。
详细说明：[VB-CABLE-NOTICE.txt](studio/licenses/VB-CABLE-NOTICE.txt)，[厂商许可页面](https://vb-audio.com/Services/licensing.htm)。
Applio 使用 MIT 许可，其第三方依赖遵循各自许可。

## 源码与构建

`studio/app.py` 是桌面界面，`worker.py` 适配实际引擎及音频，`runtime.py` 下载/分片合并/校验/安装引擎，
`driver.py` 验证及安装驱动，`setup_helper.py` 为安装向导提供组件配置（离线版还负责部署内置女声并预选主模型），
`routing.py` 识别虚拟音频设备。

在线交付脚本是 `installer/integrated.iss`（配合 `build.ps1`）；离线交付脚本是 `installer/offline.iss`
配合 `build-offline.ps1` 与 `pack-offline-sfx.ps1`。旧 `preview.iss` 不用于当前构建。

**仓库只包含源码与脚本，不含大体积/第三方二进制**（`.gitignore` 已排除 `vendor/`、`tools/InnoSetup/`、
`offline-build/` 大文件、`build/`、`dist/`、虚拟环境等）。构建前需要自行准备官方引擎分片、
两把女声模型、VB-CABLE 驱动包和 Inno Setup，放置位置、来源与校验值见 [BUILD-OFFLINE.md](BUILD-OFFLINE.md)。

在线版构建（Python 3.11/3.12 x64，虚拟环境安装 `requirements-build.txt`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
.\.build-venv\Scripts\python.exe .\tests\gui_check.py
```

离线全量包构建（产出单个约 4.7 GiB 的 ZIP，详见 [BUILD-OFFLINE.md](BUILD-OFFLINE.md)）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-offline.ps1
```

构建会先验证驱动包和签名、运行单元测试，再冻结主程序与安装助手；离线版随后编译内层 Inno 安装程序、
组装“setup + 引擎分片”文件夹并打成单个 ZIP（ZIP64）、校验完整性并生成 SHA-256。
`tests/installer_fixture.py` 只用于单独编译的安装流程测试，不会打包到交付版本。
本地已有官方完整 ZIP 时，在线安装程序可通过 `/ENGINEARCHIVE="完整路径"` 使用，仍强制校验官方哈希。

应用数据默认位于 `%LOCALAPPDATA%\RVCStudio`，包含引擎、模型、录音、缓存和日志。
卸载保留个人数据和可能被其他软件使用的 VB-CABLE。测试可通过 `RVC_STUDIO_DATA` 隔离应用数据。
