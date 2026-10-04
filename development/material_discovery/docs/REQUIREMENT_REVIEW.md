# 晶体结构发现服务（1115）：补充信息弹窗

补充确认使用 `REQUIREMENT_CONFIRMATION_ENABLED=1`、`REQUIREMENT_REVIEW_PROTOCOL=structured`，通过现有 `/start` 连接、v1 事件、单个 `element_system` 回答框和固定 `FILAMENT_SELECTION_OPTIMIZATION` 阶段交互。

只有缺少可执行起点（无化学式/元素且尚未同意开放化学空间，或明确选择 CSP 却没有化学式）才弹窗一次。问题提供三种写法：

| 用户填写 | 路线 | 约束 |
| --- | --- | --- |
| `化学式：BaTiO3` | CSP | 固定组成，探索结构 |
| `元素：Fe Cr Ni` | DNG | 允许使用列出的元素；每个都要出现时另设 `required_elements` |
| `需求：寻找低凸包能的晶体`（或 `开放探索：...`、`材料大类：...`） | 开放 DNG | 用户授权开放元素空间，生成后按已接入性质及实际结构评价 |

不带前缀的明确化学式或元素（例如 `BaTiO3`、`铁铝`、`Ag Fe`）保持兼容。模糊描述如“更稳定一些”不能自动等同于开放元素空间；同一个弹窗会返回错误，保留草稿让用户修正。需要“全部必含元素”、禁止元素、元素数或原子数等更严格约束时，可由上游结构化 `new_material` 字段提供，不能把“允许元素”误解为“必含元素”。

请求仍为 `requirement_review_request`，`schema_version: 1`，`data.fields: []`，`data.questions` 中仅有 `id: element_system`，`baseline_markdown` 展示已了解的场景及三种起点的计算内容，`allow_comment` 和 `auto_continue` 均为 `false`。前端按问题文本渲染一个文本框即可；可以在文本框外做三个快捷选项，但回传仍使用原字段：

```json
{"type":"checkpoint.reply","schema_version":1,"taskid":"t1","request_id":"请求中的原值","action":"apply","data":{"edits":[],"answers":[{"id":"element_system","answer":"化学式：BaTiO3"}]}}
```

`apply` 先检查选项再把补充与原始上文合并，重新解析后启动对应计算；`continue` 在缺少起点时返回待补充状态，不运行模型；`stop` 终止任务。有效回复发 `requirement_review_accepted`，无效回复发 `requirement_review_error`；断线不等于确认。弹窗只问一次，二次解析仍无法执行则明确说明，不无限循环。

补充内容可以附带明确性能和元素条件，例如 `化学式：LiAlO2；带隙至少3 eV` 或 `元素：Fe Cr Ni；必须包含Fe和Cr`（元素列表建议用空格或顿号分开）。弹窗前已有的数值上下限保留，和补充条件一起进入判定；仅历史上下文中的数值不会自动变成当前硬条件。模糊方向仍作为比较目标，不填造数值门槛。

开发副本保留 2026-10-01 独立测试版本的调整，状态为测试通过、待部署；部署进度见仓库部署记录。
