# OutlookRegister (增强版 · 免申请 OAuth2 + Mail-Sieve 联动)

基于 `Patchright` 反检测浏览器的微软 `@hotmail.com` / `@outlook.com` 全自动产号与长效 OAuth2 令牌提取工具。

### ✨ 核心增强特性

1. **免申请 Azure 应用，开箱即提长效 Token**：
   - 已内置开源邮件客户端 **Mozilla Thunderbird 官方白名单 `client_id` (`9e5f94bc-e8a4-4e73-b8be-63364c29d753`)** 与 `IMAP.AccessAsUser.All` + `SMTP.Send` + `offline_access` 权限。
   - 自动输出 Codex / Grok / Claude 注册机与 [Mail-Sieve](https://github.com/lzh3083/mail-sieve) 通用的 **4 段式标准卡密**：
     `email----password----9e5f94bc-e8a4-4e73-b8be-63364c29d753----refresh_token`
2. **支持 4 段式 IMAP XOAUTH2 辅助邮箱池（老号带新号滚雪球）**：
   - `Results/recovery_pool.txt` 直接支持常见的 4 段式卡密（`email----password----client_id----refresh_token`），通过 IMAP XOAUTH2 自动提取 6 位《个人 Microsoft 帐户安全代码》。
   - 每成功注册一个新邮箱，会自动追加写入 `Results/recovery_pool.txt`，实现辅助邮箱池自动滚雪球扩容。
3. **支持无显示器 Linux / 飞牛 NAS 后台虚拟桌面运行 (`run_on_feiniu.sh`)**：
   - 通过 `xvfb-run` 创建 `1920x1080x24` 虚拟真彩显示器运行 `headless=False` 的 Patchright 浏览器，绕过微软无头检测并触发免打码费的“无障碍按压验证”。
4. **自动同步至 Mail-Sieve 审查面板**：
   - 产出的 4 段式卡密除了保存在 `Results/hotmail_4part_cards.txt` 外，还会自动推送至配置的 `sync_to_mailsieve_urls` 面板入库并完成分类审查。

---

### 🚀 快速开始

```bash
# 1. 安装依赖与反检测 Chromium 内核
pip install -r requirements.txt
patchright install chromium

# 2. 放入至少 1 个带令牌的 Hotmail 作为辅助邮箱（参考 Results/recovery_pool.example.txt）
cp Results/recovery_pool.example.txt Results/recovery_pool.txt

# 3. 桌面系统直接运行：
python3 main.py

# 或者在飞牛 NAS / Linux 无显示器环境下通过 Xvfb 运行：
./run_on_feiniu.sh
```
