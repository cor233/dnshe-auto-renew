# DNSHE 免费域名自动续期（多账号 GitHub Action 版）

本项目通过 GitHub Actions 定时运行 Python 脚本，自动为 [DNSHE](https://dnshe.com/) 的免费域名进行续期。支持**多账号管理**，续期结果以 Markdown 摘要形式展示在 Actions 运行页面中。

---

## 功能特点

- 🤖 **全自动续期**：每月 1 号自动运行（可手动触发），无需人工干预。
- 🔑 **多账号支持**：通过 JSON 数组配置多个 DNSHE 账号的 API 密钥。
- 📊 **结果摘要**：运行结束后自动生成工作流摘要，清晰列出每个账号、每个域名的续期状态。
- 🛡️ **安全可靠**：API 密钥仅以 GitHub Secrets 存储，永不泄露。

---

## 前置要求

- 一个 **GitHub 仓库**（公开或私有均可）。
- 至少一个 **DNSHE 账号**，并已注册免费域名。
- 从 DNSHE 后台获取 **API Key** 和 **API Secret**（路径：免费域名页面 → 底部“API 管理” → 创建 API 密钥）。

---

## 快速开始

### 1. 添加工作流文件

在你的仓库中创建以下目录和文件：

将 [工作流模板](#工作流模板) 的内容复制到该文件中。

### 2. 添加续期脚本

在仓库根目录创建文件 `renew.py`，将 [脚本代码](#脚本代码) 完整复制进去。

### 3. 配置 Secrets

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中，点击 **New repository secret**，添加一个名为 `ACCOUNTS` 的 Secret，内容为 JSON 数组，格式如下：

```json
[
  {
    "key": "cfsd_xxxxxxxxxx",
    "secret": "yyyyyyyyyyyy"
  },
  {
    "key": "cfsd_zzzzzzzzzz",
    "secret": "wwwwwwwwwwww"
  }
]
