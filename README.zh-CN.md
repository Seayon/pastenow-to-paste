# PasteNow → Paste 剪贴板迁移工具

将 **PasteNow 2.32** 的本地历史迁入 **Paste 7.0.2**，保留原复制时间、图片、HTML/富文本、来源应用、标题和手动分类。

这是独立实现的迁移工具。通过 Python 读取数据库和附件，再使用 Swift 与苹果 Core Data 框架加载本机 Paste 自带的数据模型，在副本上转换。没有修改软件本体，也不包含厂商的数据模型文件。

## 使用

需要 macOS、Python 3.10 及以上版本，以及能运行 `xcrun swiftc` 的苹果命令行开发工具。先打开一次 Paste 完成初始化，再**退出 PasteNow 和 Paste**，保持退出状态直到安装结束。

```sh
git clone https://github.com/Seayon/pastenow-to-paste.git
cd pastenow-to-paste

python3 migrate.py inspect
python3 migrate.py prepare --workspace "$HOME/PasteMigration/work-01"
python3 migrate.py verify --workspace "$HOME/PasteMigration/work-01"
python3 migrate.py install --workspace "$HOME/PasteMigration/work-01"
```

- `inspect`：只输出数量和数据库状态。
- `prepare`：备份、解析、转换、生成搜索缓存并校验，**不替换正式数据**。
- `verify`：再次比较原始格式数据的长度和 SHA-256、时间、标题、来源、分类、搜索缓存与原有 Paste 记录。
- `install`：显式安装转换结果，保留正式库的旧副本。正式库自准备后有变化时会拒绝安装，避免覆盖新增记录。

工作目录必须是仓库和软件数据目录之外的**新目录**，并预留足够空间；备份、导出、转换和安装副本可能需要原库体积的数倍空间。工作目录含完整剪贴板内容，不要上传 GitHub 或提交到问题反馈中。

转换后的 Paste 历史期限会设为“永久”，以免旧记录启动后被自动清理。安装后请实际验证搜索、图片复制和富文本粘贴；iCloud 和其他设备的同步结果需要另行核对。

## 保留范围与限制

保留每条记录的全部原始剪贴板表示，包括图片、HTML、RTF 和应用私有格式；复制时间使用数据库的原始数值，不改为导入当天。重复文本不会合并，以免丢失不同的时间、格式或分类。稳定标识可防止本工具重复导入同一个源库的同一行，但无法自动识别其他工具或之前一次性脚本导入的记录。

文件记录保留路径和已存内容，不会额外复制路径指向的文件；已删除的文件不能凭路径恢复。手动分类及条目排序会保留，智能筛选规则、源软件隐藏状态与复制次数没有直接对应字段；次数和隐藏标记仍留在私有导出文件中。源数据库内的所有记录都会迁移。

只验证了上述版本。模型或数据结构不匹配、附件缺失、时间非法、图片无法解析时会停止，不会静默跳过。软件未公开承诺此存储格式兼容性。

## 回滚

安装时会输出 `Paste.migration-backup-<token>` 旧库位置，也会把路径记录在工作目录的 `installation.json` 中。需要恢复时，先退出两个软件，将当前 Paste 数据目录移到一旁，再把旧库目录改名恢复为 `Paste`。保留所有副本，确认恢复成功后再清理。

如果安装中断，可按 `installation.json` 中的 `rollback`、`ready` 查找旧库和待安装副本。**本地回滚不等于撤销已传播的 iCloud 数据。**

## 测试与参考

```sh
python3 -m unittest discover -s tests -v
```

测试使用人工构造数据，覆盖多种内容格式、附件、时间、WAL、损坏与篡改检测、重复运行、安装和失败回滚；安装了 Paste 对应模型时，还会运行完整的本地集成测试。公共 CI 不携带专有模型，因此跳过该集成测试。

此前一次性迁移实测了 1,825 条记录、5,786 份格式数据，内容和复制时间均一致，并验证了实际搜索与文字/图片复制。当前通用工具是后续整理实现，验证范围是人工数据集成测试，两者不混为同一次实测。

我们查看了 [Clipaste 源码](docs/clipaste-review.md)：它的 PasteNow 导入路径主要读取 JSON 文本，无法直接替代完整附件迁移。本工具独立实现，没有复制其代码。

更详细的流程、兼容范围和恢复说明见 [英文 README](README.md)。MIT 开源，与各软件厂商无隶属关系。
