# 🚀 企业级架构演进与能力建设规划 (Enterprise Architecture Future Plan)

在腾讯云亚太区 (APAC) Solutions Architect 面试中，面试官不仅看重你能否把代码写出来，更看重你对**企业级生产环境 (Enterprise Production)** 的敬畏之心。
结合 `02_gap_analysis_kevin_email.md` 的盲区，以下是咱们系统在未来三个维度的演进计划（这也是你在面试中可以脱稿演说的核心架构愿景）。

---

## 🔴 演进计划 1：基础设施即代码 (IaC) 与自动化交付
**对应盲区**：Capability Building（可复用架构模板）

### 1. 当前现状 (The Gap)
系统目前通过 `docker-compose.yml` 部署。这种方式在单机原型验证时效率极高，但不具备企业级交付能力。如果明天要为 10 个东南亚金融大客户分别部署独立的隔离环境，手动敲 docker 命令将是一场灾难。

### 2. 升级规划 (The Plan)
- **引入 Terraform / Tencent Cloud TIC**：将所有的底层云资源（如 VPC网络、子网、安全组、负载均衡 CLB、Kubernetes 集群）全部代码化。
- **构建可复用模板 (Reusable Modules)**：编写一套标准的“金融级高可用架构模板”。前线 SA 只需要修改参数（比如 `region="ap-singapore"`, `customer="DBS_Bank"`），执行 `terraform apply`，就能在 10 分钟内自动拉起一套完全隔离、合规的腾讯云专属环境。
- **GitOps 流水线**：结合 GitHub Actions，任何架构变更必须经过 Code Review 才能合并并自动生效，杜绝人工去服务器上手动改配置带来的雪崩风险。

---

## 🔴 演进计划 2：数据安全与 APAC 跨国合规
**对应盲区**：Stringent compliance / APAC MAS & PDPA 

### 1. 当前现状 (The Gap)
我们目前实现了零信任日志隧道（Tailscale PLG），但核心业务数据库 (PostgreSQL / Milvus) 的数据在磁盘上是明文存储的，且内网隔离尚未做到极致。这绝对过不了新加坡 MAS（金融管理局）或印尼 PDP 数据保护法的安全审计。

### 2. 升级规划 (The Plan)
- **数据静态加密 (Encryption at Rest)**：
  - 强制接入腾讯云 **KMS (Key Management Service)**。
  - 对于所有云盘（CBS）和对象存储（COS）开启透明加密。即使物理硬盘被盗，没有 KMS 密钥也无法读取任何客户的研报数据。
- **边界与网络隔离 (Network Boundary)**：
  - 所有微服务彻底取消公网 IP。仅暴露最外层挂载了 **WAF (Web 应用防火墙)** 的 CLB。
  - 如果跨 Region 调用（例如香港调用悉尼日志），废弃目前的公网+VPN，改用腾讯云 **CCN (云联网) / PrivateLink** 实现跨国专线直连，确保流量 100% 不经过公共互联网。
- **精细化 IAM 权限 (Least Privilege)**：
  - 目前所有容器共享宿主机权限。未来将采用 **OIDC / 角色扮演**，为每个微服务分配独立的最小权限组。例如：Engine 服务只能读取 S3 里的文件，绝对没有删除权限。

---

## 🔴 演进计划 3：FinOps 与深度成本优化
**对应盲区**：Post-Sales Architecture Optimization (Cost & Performance)

### 1. 当前现状 (The Gap)
所有组件按需全天候 24/7 运行，资源利用率（CPU/Memory）在夜间可能极低，导致云账单冗余。

### 2. 升级规划 (The Plan)
- **无状态计算节点的竞价改造 (Spot Instances)**：
  - 咱们架构中最耗费计算资源的是 `Celery Worker`（负责解析 PDF 和算 Embedding）。
  - 因为我们有 RabbitMQ 兜底，Worker 是“无状态且支持重试”的。因此，可以将 Worker 节点全面迁移到**竞价实例 (Spot Instances)**，这能直接节省 80% 的计算成本。即使实例被云厂商突然回收，RabbitMQ 也会自动把任务派发给其他存活的 Worker。
- **自动弹性伸缩 (Auto-Scaling)**：
  - 根据 Grafana 中 RabbitMQ 的 `queue_depth` (队列积压长度) 触发 HPA (Horizontal Pod Autoscaler)。任务多时秒级扩容 100 个 Worker，任务清空后缩容到 1 个。
- **冷热数据生命周期管理 (Data Lifecycle)**：
  - 金融研报时效性强。通过设置 COS 的生命周期策略，将 90 天前的分析报告和 PDF 文件，自动从标准存储降级到**归档存储 (Archive / Glacier)**，大幅压降长期的 Storage 成本。
