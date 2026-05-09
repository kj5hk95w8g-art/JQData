# REQ-004: 轻量级数据分发平台（完整方案）

> 状态：需求已明确，待开发  
> 最后更新：2026-05-09

---

## 一、目标

公司内部版 akshare：团队成员 pip install 后开箱即用，像 akshare 一样无配置、无感知。

---

## 二、核心原则（已确认）

| 原则 | 说明 |
|------|------|
| **不做用户管理** | 无注册/登录/付费系统 |
| **不做手动发Token** | 靠请求签名实现无感知认证 |
| **不公开宣传** | GitHub私有仓库，不推广 |
| **不上PyPI** | pip install from git |

---

## 三、技术方案

### 3.1 认证：请求签名

**原理：** SDK内置盐值，每次请求自动计算签名，服务器验证。

```
SDK内置盐值（SECRET_SALT）
    ↓
每次请求：signature = md5(SALT + timestamp)
    ↓
请求头携带 X-Timestamp + X-Signature
    ↓
服务器验证签名 + 时间窗口（±5分钟）
```

**为什么不用明文Token：** 签名每次不同，抓包无法重放；反编译也看不到固定凭证。

**盐值更换频率：每年一次**（用户确认）
- 服务器同时支持新旧盐值并行7天
- 发新版SDK → 通知团队成员 `pip install --upgrade`
- 7天后禁用旧盐值

### 3.2 入口：弹性公网IP（待定）

| 阶段 | 方案 | 状态 |
|------|------|------|
| **当前** | 内网IP访问（172.24.52.237:8000）| 已可用 |
| **后期** | 绑定弹性公网IP | 待定，后续增加 |

**说明：** REQ-004 开发不阻塞于公网IP。先完成内网可用，公网IP绑定后自动生效（base_url 内置在SDK中，换IP后更新SDK即可）。

### 3.3 SDK安装与使用

**安装：**
```bash
pip install git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

**使用（完全无感知）：**
```python
import jqdata_sdk as jq

# 无需auth()，内置base_url和签名逻辑
df = jq.get_price("000001.XSHE", start_date="2020-01-01", end_date="2026-05-08")
```

**升级：**
```bash
pip install --upgrade git+ssh://git@github.com:kj5hk95w8g-art/JQData.git#subdirectory=src/sdk
```

### 3.4 安全防护层次

| 层次 | 机制 | 作用 |
|------|------|------|
| **第一层** | 请求签名 | 阻止无签名的随机扫描 |
| **第二层** | 时间窗口（±5分钟） | 防止抓包重放攻击 |
| **第三层** | 单IP限流（100条/秒） | 防止单个IP刷爆 |
| **第四层** | 不公开宣传 | 降低被发现概率 |

---

## 四、团队成员接入流程

### 4.1 团队成员：生成SSH key

**Mac/Linux 用户：**

```bash
# 1. 打开终端，执行以下命令（邮箱替换为自己的）
ssh-keygen -t ed25519 -C "your_name@company.com"

# 2. 提示保存位置时，直接回车（使用默认路径）
#    Enter file in which to save the key (/Users/xxx/.ssh/id_ed25519):

# 3. 提示输入密码时，直接回车（不设置密码，方便pip install）
#    Enter passphrase (empty for no passphrase):
#    Enter same passphrase again:

# 4. 查看生成的公钥内容
 cat ~/.ssh/id_ed25519.pub

# 5. 复制输出的内容，类似下面这行：
#    ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIxxx your_name@company.com
```

**Windows 用户：**

```bash
# 1. 打开 Git Bash 或 PowerShell，执行
ssh-keygen -t ed25519 -C "your_name@company.com"

# 2. 保存位置按回车默认
# 3. 密码直接回车（不设置）

# 4. 查看公钥
 type %USERPROFILE%\.ssh\id_ed25519.pub

# 5. 复制输出的完整内容
```

### 4.2 团队成员：发邮件申请

**收件人：** 仓库管理员（你）  
**主题：** 申请 JQData SDK 权限  
**正文：**

```
您好，申请 JQData SDK 使用权限。

姓名：张三
部门：投资部
邮箱：zhangsan@company.com

SSH公钥内容如下：
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIxxx zhangsan@company.com

谢谢！
```

### 4.3 管理员：开通权限

```
你（仓库管理员）
    │
    └── 1. 登录GitHub → JQData仓库 Settings → Deploy keys
           → Add deploy key
           → Title: 张三-投资部
           → Key: 粘贴邮件中的公钥内容
           → Allow write access: ❌ 不勾选（只读即可）
           → Add key
    │
    └── 2. 回复邮件告知已开通，附安装命令
```

---

## 五、风险与应对

| 风险 | 应对方案 |
|------|---------|
| **盐值泄露** | 每年换盐值；服务器新旧并行7天；发新版SDK |
| **时间不同步** | NTP自动同步；5分钟窗口足够容错 |
| **DDoS攻击** | 单IP限流；极端情况换弹性公网IP |
| **代码泄露** | 定期换盐值；离职移除GitHub Deploy Key |
| **GitHub访问失败** | 保留本地wheel打包作为备用分发方式 |

---

## 六、不做清单

以下功能**明确不做**：

- ❌ 用户注册/登录系统
- ❌ 付费/积分/额度售卖
- ❌ 手动Token分发
- ❌ 每日全局额度限制（内部使用，单IP限流足够）
- ❌ 独立文档站（GitHub README足够）
- ❌ 用户管理后台
- ❌ 域名备案（先用IP，后期可选）

---

## 七、验收标准

- [ ] 团队成员在内外网都能 pip install 后直接使用
- [ ] 无签名请求返回 401
- [ ] 过期时间戳请求返回 401
- [ ] 错误签名请求返回 401
- [ ] 单IP超过100条/秒返回 429
- [ ] 正常查询返回 200 + 正确数据
- [ ] `pip install --upgrade` 升级生效
- [ ] 盐值更换流程可正常执行（新旧并行→禁用旧版）

---

## 八、依赖

| 需求 | 状态 | 说明 |
|------|------|------|
| REQ-002 | ✅ 已完成 | REST API + SDK框架已有 |
| REQ-003 | ⚠️ 进行中 | 数据全量同步中，不影响REQ-004开发 |
| 弹性公网IP | ⏸️ 待定 | 不阻塞开发，内网可先验证 |

---

## 九、变更记录

| 日期 | 变更 |
|------|------|
| 2026-05-09 | 需求明确：请求签名 + 内置base_url + pip from git |
| 2026-05-09 | 确认：盐值每年更换、SSH key邮件收集、公网IP待定 |
