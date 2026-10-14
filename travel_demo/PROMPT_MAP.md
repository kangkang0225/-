# 首版提示词来源与适配

这是旅行照片访谈网页 Demo。原文保存在 `sources/`，来源、页节定位及 SHA-256 保存在 `sources/manifest.json`。运行时提示词由 `prompts.py` 从归档原文截取，再叠加项目约束。归档原文不会被适配代码修改。

| 模块 | 采用的来源 | 保留与适配 |
|---|---|---|
| 照片线索 | TRAVOT §3.2.1 图像描述任务 | 原论文采用图像描述模型，没有公开对应的视觉 LLM 完整提示词。这里的可见场景/细节/不确定项任务是项目新增适配，不能称为作者原 prompt。无固定旅行问题种子。 |
| 情绪识别 | GuideLLM 附录 D.2 Emotion Detection | 保留原情绪类别与强度识别任务；合并为一次调用，原 EmoLlama 改为 MiniMax。仅帮助承接当轮表达，不推断用户真实心理状态，不启用治疗性 CBT/心理动力模块。 |
| 访谈 | StorySage `interviewer/prompts.py` CONTEXT、INSTRUCTIONS | 保留自然语气、最新细节追问、避免重复措辞、根据投入度调整。自传语境适配为旅行；允许仅承接、拒答、跳过、收束；不强制切题或照片覆盖。 |
| 后台记录 | GuideLLM 附录 B Extract Events / Explore；StorySage scribe 的隐含回答原则 | 保留事件字段及基于已有材料的追问任务。取消人生阶段覆盖；新增用户纠正、节点合并拆分、来源 seq、片段充分性与最终选材。候选问题由访谈员判断使用。 |
| 三版微叙事 | Micro-narratives 附录 A Narrative Creation / Adaptation；StorySage writer Information Accuracy | 原三角色为专家、14 岁朋友、23 岁朋友，原任务是青少年社交媒体困难经历，不是游记。运行时截取贴近用户语言、描述性表达、保留语言和内容的原句，完整角色原文仅归档。“原话整理/自然叙述/文学表达”是本项目确认的适配，取消专家与年龄角色、原社交媒体问题槽和示例中的未经用户提供的升华。 |
| 最终游记 | 旧项目 `travelog-writer/references/heartfelt-visit.md` 的 Material Organization / Voice and Form | 这是用户现有项目的旅行规则，非论文作者原 prompt。依据记录员所选节点、所选微叙事与原话写作；专业性落实在选材、详略、句序和节奏。没有使用 GuideLLM 附录 E 的自传整篇模板。 |

共用项目约束：未知留空；照片观察与个人经历分开；用户纠正/拒答优先；助手问句不是事实；禁止为了顺畅新增行为、感受、因果、天气、人物关系或感悟。原论文任务与 JSON 输出协议、调度策略、超时恢复是不同层次：后者属于实现适配。

## 复现定位

- StorySage 作者仓库：https://github.com/ShayanTalaei/StorySage
- GuideLLM：https://aclanthology.org/2025.naacl-long.287.pdf；本地 Zotero 原 PDF 已用于文本抽取，归档 B–D。
- Micro-narratives 附录 A：从 Europe PMC `PMC12265993/supplementaryFiles` 官方镜像获取原补充 PDF，PDF 和文本均已归档。
- TRAVOT：Zotero `NX3SPU2Q` 的 *Conversational Robot System for Travel Memoir Generation*，§3.2.1、§3.3、Listings 2–7；没有将论文未披露的完整生成模板当作原始提示词。

`scripts/build_travel_prompt_sources.py` 可以重新归档已定位来源，须能够访问原文件及 Europe PMC。运行正常 Demo 不需要联网下载论文。

## 尚不能据此宣称的结论

源码复用与流程检查不证明访谈质量、专利效果、低时延达标或文学表达事实一致。需要真实照片与用户试测，对重复、承接、贴题、等待及新增事实逐例评价。配置默认值是试测起点，不是实验结果或冻结阈值。
