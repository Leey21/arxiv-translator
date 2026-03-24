---
name: arxiv-translator
description: 将 arXiv 论文自动翻译为中文 PDF。触发后按本 skill 四步顺序直接执行，勿长篇规划。用户提供论文标题或 arXiv ID、说「翻译论文」「我想读中文版」等时立即使用。支持多篇论文处理。无需用户手动操作。
---

# arXiv 论文中文翻译

**目标：** 将指定论文的 LaTeX 源码译为中文，并编译得到 PDF。

**流程：** 须严格按下文「第一步」至「第四步」顺序执行，不得擅自省略、合并或调换步骤。

**交互：** 仅在论文 ID 无法确定、检索结果存在多个需用户择一才可向用户提问；其余情况一律无中断的执行得到最终翻译后的PDF。

**翻译：** 翻译全部由当前对话模型自身完成，严禁使用外部翻译工具以及下载已有的翻译版本。

---

## 第一步：确定论文 ID

- arXiv URL/ID → 直接提取 ID
- 论文标题 → 搜索 arXiv / 网页查找 ID；找不到时给出候选让用户确认

---

## 第二步：获取源码并确定翻译范围

```bash
python3 {SKILL_DIR}/scripts/download.py "{PAPER_ID}" "$OUTPUT_DIR/.tmp_arxiv/{PAPER_ID}"
```

`download.py` 一步完成：下载源码 → 解压 → 递归查找 `.tex` → 定位主文件 → 提取标题。

`OUTPUT_DIR` 为用户指定的保存路径，未指定则为当前目录

无源码（仅 PDF）则告知用户跳过。

脚本向 stdout 输出三行，格式如下：
```
WORK_DIR=<源码目录绝对路径>
MAIN_TEX=<主文件相对路径>
PDF_NAME=<论文标题>
```
---

## 第三步：翻译

由当前**对话模型**直接在原 `.tex` 文件上进行翻译修改，按以下规则翻译：

- **翻译范围：** 默认只翻正文，不翻附录，但附录中的内容需要得到保留，若同一文件中出现 `\appendix`，默认只翻该命令之前的内容。用户明确要求“翻译全文”时才翻附录。
- **必须翻译：** 正文叙述、摘要、图表标题、列表项、脚注中的描述文本，以及代码块中的注释。
- **保留不翻：** 数学环境、LaTeX 命令、`\cite{}`/`\ref{}`/`\label{}`、图片路径、URL、代码本体、`.bib`、人名、机构名、模型名、数据集名。
- **专有名词：** Transformer、Softmax、Token 等通用学术术语保留英文，不要生硬硬译。
- **标题要求：** `\title{}` 须改为自然中文题名，不保留英文原题或中英并列；输出 PDF 文件名仍使用第二步的 `PDF_NAME`。
- **多篇处理：** 多篇论文可以分别处理；只有在用户**明确要求**并行委派时，才开启多个 subagent，否则直接顺序完成。

译后必须做自检：

```bash
python3 {SKILL_DIR}/scripts/inspect_tex.py scan "$WORK_DIR" "$MAIN_TEX" body
```

若用户明确要求“翻译全文”，则改为：

```bash
python3 {SKILL_DIR}/scripts/inspect_tex.py scan "$WORK_DIR" "$MAIN_TEX" full
```

脚本会输出 `SUSPECT_COUNT=<数字>` 以及若干 `SUSPECT=<文件>:<行号>:<片段>`。
- 只要 `SUSPECT_COUNT` 非 0，就必须逐条回到对应位置进行翻译；
- 只有 `SUSPECT_COUNT=0`，或剩余项明确属于“保留不翻”范围时，才可进入第四步。

---

## 第四步：编译与清理

编译：

```bash
python3 {SKILL_DIR}/scripts/compile.py "$WORK_DIR" "$MAIN_TEX" "$OUTPUT_DIR/$PDF_NAME.pdf"
```

`compile.py` 会统一完成以下编译前处理：
- 若检测到中文且主文件尚无 CJK 支持，自动在主文件 preamble 中补入 LuaLaTeX 所需中文支持；
- 自动注释掉与 Unicode 编译栈冲突的 `fontenc` / `inputenc`；
- 自动识别 `bibtex` / `biber` / 已内置 `.bbl` 的情况；
- 自动忽略常见编译中间文件与未被源码引用的游离 PDF，避免把无关产物上传到远端编译服务。

编译失败时：读取 stderr 中的错误日志，参考 `references/compile-errors.md` 修复源码，重新编译（最多重试 2 次）。

编译成功后清理掉中间文件：

```bash
python3 {SKILL_DIR}/scripts/cleanup.py "$OUTPUT_DIR"
```

多篇论文时，所有论文都完成 PDF 编译并保存后再进行中间文件清理。

最后输出 PDF 保存路径。

---

## 参考文件
- `references/compile-errors.md`：编译常见错误及修复方法
