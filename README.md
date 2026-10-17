# FlClash Lite

[FlClash](https://github.com/chen08209/FlClash) 在部分 Android 手机上会出现安装失败。为尝试解决这类问题，FlClash Lite 基于上游源码，**更换 Android 包名并使用独立签名重新构建安装包**，保留上游代理功能，并自动同步上游 Release。

相关背景：[FlClash 安装失败问题](https://huarun.win/articles/flclash-install-failed)。

## 项目重点

本项目当前仅做以下三项定制，**除此之外，不修改上游应用的其他代码**：

1. **更换包名和签名**：Android 包名固定为 `com.follow.clash.v1`，使用独立签名重新构建；后续版本沿用相同包名和签名，以支持覆盖升级。
2. **移除首次启动的强制免责声明确认**：Android 首次打开直接进入应用，无需滚动阅读、点击同意或复述声明；仍可在 **工具 → 免责声明** 中查看声明。
3. **应用内更新指向本仓库**：自动检查、手动检查及“前往下载”均使用 FlClash Lite Releases，“关于”页的项目链接也指向本仓库。

应用名称、图标、代理功能、配置格式及版本号均沿用上游。

## 发布方式

每六小时检查上游 Release，重建三个架构的 Android APK；其他平台附件原样同步，并更新 SHA-256 校验文件。

构建时运行源码分析和测试，并校验 APK 的包名与签名。

## 下载与迁移

从 [FlClash Lite Releases](https://github.com/freephilx/FlClash-Lite/releases) 下载 Android APK。

原版 FlClash 与 FlClash Lite 的 Android 包名不同，可以同时安装，但数据不会自动继承。首次迁移请按以下步骤操作，菜单名称以 v0.9.0 简体中文界面为准。

### 在旧版导出数据

1. 打开仍保存着原有数据的 FlClash，进入 **工具 → 备份与恢复**。
2. 在 **本地** 分组点击 **备份**，通过系统文件选择器，将生成的 `FlClash_backup_….zip` 保存到「下载」等新旧应用都能选择的目录。保留完整 ZIP，无需解压。
3. 等待出现 **备份成功**，确认文件已保存，再停止旧版的代理服务。先保留旧版，待新版恢复并验证正常后再卸载。

### 在新版导入数据

1. 安装并打开从本仓库下载的 FlClash Lite，进入 **工具 → 备份与恢复**。应用显示名称仍为 FlClash，新版包名为 `com.follow.clash.v1`。
2. 在 **本地** 分组点击 **恢复**，在弹窗中选择 **恢复所有数据**，再选择旧版导出的备份 ZIP。
3. 等待出现 **恢复成功**，检查配置、订阅和应用设置是否齐全，再启动新版的代理服务，并按系统提示重新授予 VPN 权限。通知、自启动和后台运行等系统权限也需在新版中重新确认。

完整迁移请选择 **恢复所有数据**；**仅恢复配置文件** 不会恢复应用设置。如果新版已有需要保留的数据，请先在新版执行一次备份。若提示「该备份来自更高版本的应用」，先更新 FlClash Lite 再恢复。

后续升级 FlClash Lite 时，同包名、同签名的 APK 可以直接覆盖安装，保留现有数据，无需重复导出、导入。

本次重建的 v0.9.0 和 v0.9.1 Android APK，应用内更新检查及下载入口均指向本仓库。此前安装的旧 APK 不会自动获得此修改，请先从本仓库 Releases 手动下载并覆盖安装一次；之后可通过应用内检查获取本项目的新版本。

## 源码与构建

`upstream/` 通过 Git submodule 引入上游源码，`patches/` 保存定制补丁。构建时只修改临时副本，保留上游源码原样；`releases/` 记录源码提交、补丁、签名和附件摘要，便于追溯。

当前版本使用 `patches/` 下的三个补丁，仅调整 Android 包名、首次启动声明和应用内更新仓库。历史版本补丁及配套处理归档在 `patches/legacy/`，用于复现已发布旧版，不参与 v0.9.1 正式版及之后版本的构建。详见 [补丁说明](patches/README.md)。

```bash
git clone https://github.com/freephilx/FlClash-Lite.git
cd FlClash-Lite
git submodule update --init upstream
```

GitHub 自动生成的 Source code 压缩包不含 submodule 内容，请按上述方式获取源码。

构建入口为 [scripts/project.py](scripts/project.py)，提供 `prepare`、`check`、`build` 和 `verify` 命令。需准备 Python 3.10+、GitHub CLI、上游指定版本的 Flutter / Go、JDK 17+、Android SDK / NDK 和 Rust 工具链。自动构建流程见 [sync.yml](.github/workflows/sync.yml)。

签名私钥和密码保存在仓库外的 `~/.config/flclashx/`，通过 GitHub Actions Secrets 注入构建。仓库仅保存公开证书；维护者需备份原密钥，以保持后续版本的签名一致。

## 许可

遵循上游 [GPL-3.0](LICENSE) 许可证，源码与 Release 均公开。
