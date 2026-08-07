# 非图像功能演示输入

这些文件只用于本机功能测试，内容为虚构示例，不包含真实敏感信息。

## 功能与输入位置

| 工具 | 功能 | 在网页中如何输入 | 使用文件 |
|---|---|---|---|
| Synthetic Data Kit | 普通 QA | 在“选择输入”中上传 Markdown | `01-高质量数据平台试点说明.md` |
| Synthetic Data Kit | CoT 思维链 | 在“选择输入”中上传 Markdown | `01-高质量数据平台试点说明.md` |
| Synthetic Data Kit | 文档摘要 | 在“选择输入”中上传 Markdown | `01-高质量数据平台试点说明.md` |
| Synthetic Data Kit | 补充 CoT | 在“选择输入”中上传 JSON | `02-CoT增强输入.json` |
| Easy Dataset | 文档问答 | 在“文档问答”中上传 Markdown | `01-高质量数据平台试点说明.md` |
| Easy Dataset | 数据蒸馏 | 不上传文件，把 TXT 中的一行粘贴到“领域主题” | `03-数据蒸馏主题.txt` |
| Easy Dataset | 评估数据 | 在“评估数据”中上传 Markdown | `01-高质量数据平台试点说明.md` |
| SynLogic | Arrow Maze | 不需要文本，直接设置数量、宽高和填充率 | 无 |
| KAQG | 图谱增强单选题 | 在 KAQG 页上传带文本层 PDF | `kaqg/数据平台运维与质量规范.pdf` |
| Cleanlab | 标签、异常和重复检查 | 在 Cleanlab 页上传 CSV/JSONL | `cleanlab/数据质量工单.csv` |
| Cleanlab | 外部预测概率 | 先上传主数据，再上传概率 JSONL | `cleanlab/示例预测概率.jsonl` |

## 建议的最小验证参数

- Synthetic 普通 QA：首次连通测试用 `1` 条、筛选阈值 `1.0`；确认成功后再改为 `3` 条和 `7.0`。
- Synthetic CoT：首次连通测试用 `1` 条、筛选阈值 `1.0`；确认成功后再改为 `3` 条和 `7.0`。
- Synthetic 摘要：直接使用默认参数。
- Synthetic 补充 CoT：先生成 `1` 条，确认格式后再增加。
- Easy Dataset 文档问答：先生成 `3` 条，选择“单轮问答”。
- Easy Dataset 数据蒸馏：标签层级 `1`、每层标签数 `2`、每标签问题数 `1`。
- Easy Dataset 评估数据：先勾选“单选题”和“简答题”，每类 `1` 条。
- SynLogic：`20` 条、`5×5`、填充率 `0.3–0.9`。
- KAQG：领域“数据平台运维规范”，低、中、高难度各 `3` 题。
- Cleanlab：列名 `id / text / label`，首次使用自动训练；再用概率文件验证高级入口。

## 注意

- 同一个本机文件需要分别上传到各工具项目，因为五个工具的项目文件彼此隔离。
- “补充 CoT”不能上传 DOCX、PDF 或 Markdown。它只处理已经存在的问题和答案 JSON。
- 多模态 QA 和图片问答不在本套演示数据范围内。
